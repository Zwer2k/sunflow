"""Background loop: periodic planning and shadow recording."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from .config_store import ConfigStore, DeviceConfig, SunflowConfig
from .control import PlanController
from .discovery import Snapshot, SnapshotCache, fetch_snapshot
from .ha import HAClient, HAError
from .load_model import LoadModel, fit as fit_load_model
from .plan import PlanResult, compute_plan
from .profiles import ProfileLibrary
from .pv_learning import PvCorrection, learn, learn_planes
from .resolver import preview
from .shadow import Sample, ShadowStore, SlotRecorder, slot_start
from .site import device_parameters, measurement_device, pv_strings, site_status
from .vehicles import unplugged_choices

_LOGGER = logging.getLogger(__name__)

SAMPLE_SECONDS = 60


async def local_timezone(client: HAClient) -> ZoneInfo:
    """Return Home Assistant's configured time zone."""
    config = await client.command("get_config")
    return ZoneInfo(config["time_zone"])


# PV correction: learned from this many days of shadow data, refreshed this often
LEARNING_WINDOW = timedelta(days=120)
CORRECTION_REFRESH = timedelta(hours=1)
# A failed replan runs every query again; not every minute while Home Assistant has a
# problem. The pause doubles with each failure up to the maximum
REPLAN_RETRY = timedelta(minutes=5)
REPLAN_RETRY_MAX = timedelta(minutes=30)


async def current_plan(
    client: HAClient,
    store: ConfigStore,
    library: ProfileLibrary,
    snapshots: SnapshotCache | None = None,
    pv_correction: PvCorrection | None = None,
    load_model: LoadModel | None = None,
    plane_corrections: dict[str, PvCorrection] | None = None,
) -> PlanResult:
    """Plan from the current slot on with live data."""
    # Fresh states for the plan, registries from the shared cache
    snapshot = await snapshots.get() if snapshots else await fetch_snapshot(client)
    return await compute_plan(
        client,
        store.load(),
        library.profiles,
        snapshot,
        slot_start(datetime.now(UTC)),
        await local_timezone(client),
        pv_correction,
        load_model,
        plane_corrections,
    )


async def take_sample(
    client: HAClient, store: ConfigStore, library: ProfileLibrary, snapshots: SnapshotCache | None = None
) -> Sample | None:
    """Read SoC, battery, grid and load of the battery inverter and the site PV."""
    config = store.load()
    profiles = library.profiles
    device = next(
        (d for d in config.devices if "battery_window" in profiles[d.profile]["capabilities"]), None
    )
    if device is None:
        return None
    snapshot = await snapshots.get() if snapshots else await fetch_snapshot(client)

    def values(source: DeviceConfig | None) -> dict[str, float | None]:
        if source is None:
            return {}
        return {
            r.role: r.value
            for r in preview(
                profiles[source.profile],
                snapshot,
                source.ha_device_id,
                {k: o.model_dump() for k, o in source.role_overrides.items()},
            )
        }

    roles = values(device)
    params = {p["name"]: p["effective"] for p in device_parameters(profiles[device.profile], device, snapshot)}
    capacity = params.get("capacity_wh")
    soc = roles.get("battery_soc")
    return Sample(
        soc_wh=capacity * soc / 100 if capacity and soc is not None else None,
        battery_w=roles.get("battery_power"),
        grid_w=values(measurement_device(config, profiles, "grid")).get("grid_power"),
        pv_w=site_status(config.devices, profiles, snapshot).pv_total,
        load_w=values(measurement_device(config, profiles, "load")).get("load_power"),
        loadpoint_w=consumer_power(config, profiles, values),
        strings=string_powers(config, profiles, snapshot),
    )


def string_powers(config: SunflowConfig, profiles: dict[str, Any], snapshot: Any) -> dict[str, float]:
    """DC power per PV string of all plants, keyed "<device id>:<string>"."""
    result = {}
    for device in config.devices:
        profile = profiles[device.profile]
        if "pv" not in profile["capabilities"]:
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        for string in pv_strings(roles):
            result[f"{device.id}:{string['index']}"] = string["power_w"]
    return result


def consumer_power(config: SunflowConfig, profiles: dict[str, Any], values: Any) -> float | None:
    """Summed power of the controllable consumers; None without any."""
    powers = []
    for device in config.devices:
        capabilities = profiles[device.profile]["capabilities"]
        if "loadpoint" in capabilities or "load" in capabilities:
            readings = values(device)
            power = readings.get("charge_power") if "loadpoint" in capabilities else readings.get("power")
            powers.append(max(power or 0.0, 0.0))
    return sum(powers) if powers else None


@dataclass(kw_only=True)
class RunnerStatus:
    """State of the background loop for the UI."""

    running: bool = False
    since: datetime | None = None
    last_plan: datetime | None = None
    last_error: str | None = None


class ShadowRunner:
    """Samples every minute, records slots and replans at each slot boundary."""

    def __init__(
        self,
        client: HAClient,
        store: ConfigStore,
        library: ProfileLibrary,
        shadow: ShadowStore,
        snapshots: SnapshotCache | None = None,
    ) -> None:
        """Initialize."""
        self._client = client
        self._store = store
        self._library = library
        self.shadow = shadow
        self.snapshots = snapshots
        # Set by the app; carries out the plan where the user switched control on
        self.controller: PlanController | None = None
        self.status = RunnerStatus()
        self.latest: PlanResult | None = None
        # Latest live reading, to show what the battery really does next to the plan
        self.last_sample: Sample | None = None
        self.last_sample_at: datetime | None = None
        self._correction: PvCorrection | None = None
        self.load_model: LoadModel | None = None
        self.plane_corrections: dict[str, PvCorrection] = {}
        self._correction_at = datetime.min.replace(tzinfo=UTC)
        self._recorder: SlotRecorder | None = None
        self._replan_task: asyncio.Task[None] | None = None
        # Slot of the last successful plan; a failed replan is retried on the next tick
        self._planned_slot: datetime | None = None
        # A change arrived while a replan was running: plan once more afterwards
        self._replan_again = False
        self._replan_retry_at = datetime.min.replace(tzinfo=UTC)
        self._replan_failures = 0

    async def run(self) -> None:
        """Run until cancelled."""
        self.status.running = True
        self.status.since = datetime.now(UTC)
        try:
            while True:
                try:
                    await self._tick()
                except Exception:
                    # Never end the loop: a frozen plan until the next restart is worse
                    _LOGGER.exception("Shadow tick failed unexpectedly")
                await asyncio.sleep(SAMPLE_SECONDS - datetime.now(UTC).second % SAMPLE_SECONDS)
        finally:
            self.status.running = False

    async def _tick(self) -> None:
        if not self._client.connected:
            return
        now = datetime.now(UTC)
        start = slot_start(now)
        if self._recorder is None or self._recorder.start != start:
            await self._finish_slot()
            self._recorder = SlotRecorder(start=start)
        # Measuring goes on while planning fails, so the shadow data has no holes
        if self._planned_slot != start and now >= self._replan_retry_at:
            try:
                await self._replan(start)
                self.status.last_error = None
                self._replan_failures = 0
            except (HAError, KeyError, ValueError) as err:
                pause = min(REPLAN_RETRY * 2**self._replan_failures, REPLAN_RETRY_MAX)
                self._replan_failures += 1
                _LOGGER.warning("Replan failed, retrying in %s: %s", pause, err)
                self.status.last_error = f"Plan: {err}"
                self._replan_retry_at = now + pause
        try:
            if (sample := await take_sample(self._client, self._store, self._library, self.snapshots)) is not None:
                self._recorder.samples.append(sample)
                self.last_sample = sample
                self.last_sample_at = datetime.now(UTC)
        except (HAError, KeyError, ValueError) as err:
            _LOGGER.warning("Sample failed: %s", err)
        if self.snapshots is not None:
            try:
                snapshot = await self.snapshots.get()
                self._end_vehicle_choices(snapshot)
                if self.controller is not None:
                    await self.controller.tick(self._store.load(), self.latest, self.status.last_plan, snapshot, now)
            except HAError as err:
                _LOGGER.warning("Control step failed: %s", err)

    def _end_vehicle_choices(self, snapshot: Snapshot) -> None:
        """A vehicle chosen in the control tab counts until it is unplugged."""
        config = self._store.load()
        if not config.selected_vehicles:
            return
        connected = {}
        for device in config.devices:
            if device.id in config.selected_vehicles:
                roles = {
                    r.role: r
                    for r in preview(
                        self._library.profiles[device.profile],
                        snapshot,
                        device.ha_device_id,
                        {k: o.model_dump() for k, o in device.role_overrides.items()},
                    )
                }
                if "connected" in roles and roles["connected"].raw_state in ("on", "off"):
                    connected[device.id] = roles["connected"].raw_state == "on"
        if ended := unplugged_choices(config, connected):
            for wallbox_id in ended:
                config.selected_vehicles.pop(wallbox_id)
            self._store.save(config)

    async def _finish_slot(self) -> None:
        if self._recorder is None or not self._recorder.samples:
            return
        await asyncio.to_thread(self.shadow.store_actual, self._recorder.start, self._recorder.summary())

    def request_replan(self) -> None:
        """Replan right away (after a change in the control tab), in the background."""
        if self._replan_task is not None and not self._replan_task.done():
            self._replan_again = True
            return
        self._replan_task = asyncio.create_task(self._replan_now())

    async def ensure_plan(self) -> PlanResult | None:
        """Return the latest plan, computing one (shared with other callers) if there is none."""
        if self.latest is None:
            self.request_replan()
            if self._replan_task is not None:
                await asyncio.shield(self._replan_task)
        return self.latest

    async def _replan_now(self) -> None:
        while True:
            self._replan_again = False
            try:
                await self._replan(slot_start(datetime.now(UTC)))
                self.status.last_error = None
            except Exception as err:  # noqa: BLE001 - logged, the next tick retries
                _LOGGER.warning("Replan failed: %s", err)
                self.status.last_error = f"Plan: {err}"
            if not self._replan_again:
                return

    def relearn(self) -> None:
        """Learn again with the next plan instead of within the hour."""
        self._correction_at = datetime.min.replace(tzinfo=UTC)

    async def pv_correction(self) -> PvCorrection | None:
        """Correction learned from the shadow data, refreshed hourly."""
        now = datetime.now(UTC)
        if self._correction is None or now - self._correction_at > CORRECTION_REFRESH:
            rows = await asyncio.to_thread(self.shadow.rows, now - LEARNING_WINDOW)
            tz = await local_timezone(self._client)
            self._correction = learn(rows, tz)
            config = self._store.load()
            self.plane_corrections = learn_planes(
                rows,
                {plane.key: plane.strings for plane in config.forecast_planes if plane.strings},
                tz,
                {plane.key: plane.learn_since for plane in config.forecast_planes if plane.learn_since},
            )
            self.load_model = await asyncio.to_thread(
                fit_load_model, rows, tz, self._store.load().optimizer.large_load_threshold_w
            )
            self._correction_at = now
        return self._correction

    async def _replan(self, start: datetime) -> None:
        correction = await self.pv_correction()
        result = await current_plan(
            self._client, self._store, self._library, self.snapshots, correction, self.load_model, self.plane_corrections
        )
        self.latest = result
        self.status.last_plan = datetime.now(UTC)
        self._planned_slot = start
        if not result.plan.slots:
            return
        first = result.plan.slots[0]
        planned: dict[str, Any] = {
            "action": first.action,
            "power": first.action_power_w,
            "soc": first.soc_wh,
            "pv": first.pv_w,
            "load": first.load_w,
            "price": first.price,
            # Learning compares the uncorrected forecast with the measurement
            "forecast_pv": result.raw_pv[0] if result.raw_pv else None,
            "cloud": result.clouds[0] if result.clouds else None,
            "temp": result.temperatures[0] if result.temperatures else None,
            "planes": json.dumps(result.plane_forecasts_now),
        }
        await asyncio.to_thread(self.shadow.store_planned, start, planned)
