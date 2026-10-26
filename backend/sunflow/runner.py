"""Background loop: periodic planning and shadow recording."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from .config_store import ConfigStore, DeviceConfig, SunflowConfig
from .discovery import SnapshotCache, fetch_snapshot
from .ha import HAClient, HAError
from .plan import PlanResult, compute_plan
from .profiles import ProfileLibrary
from .resolver import preview
from .pv_learning import PvCorrection, learn
from .shadow import Sample, ShadowStore, SlotRecorder, slot_start
from .site import device_parameters, measurement_device, site_status

_LOGGER = logging.getLogger(__name__)

SAMPLE_SECONDS = 60


async def local_timezone(client: HAClient) -> ZoneInfo:
    """Return Home Assistant's configured time zone."""
    config = await client.command("get_config")
    return ZoneInfo(config["time_zone"])


# PV correction: learned from this many days of shadow data, refreshed this often
LEARNING_WINDOW = timedelta(days=60)
CORRECTION_REFRESH = timedelta(hours=1)


async def current_plan(
    client: HAClient,
    store: ConfigStore,
    library: ProfileLibrary,
    snapshots: SnapshotCache | None = None,
    pv_correction: PvCorrection | None = None,
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
    )


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
        self.status = RunnerStatus()
        self.latest: PlanResult | None = None
        # Latest live reading, to show what the battery really does next to the plan
        self.last_sample: Sample | None = None
        self.last_sample_at: datetime | None = None
        self._correction: PvCorrection | None = None
        self._correction_at = datetime.min.replace(tzinfo=UTC)
        self._recorder: SlotRecorder | None = None
        self._replan_task: asyncio.Task[None] | None = None
        # Slot of the last successful plan; a failed replan is retried on the next tick
        self._planned_slot: datetime | None = None
        # A change arrived while a replan was running: plan once more afterwards
        self._replan_again = False

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
        try:
            if self._recorder is None or self._recorder.start != start:
                await self._finish_slot()
                self._recorder = SlotRecorder(start=start)
            if self._planned_slot != start:
                await self._replan(start)
            if (sample := await take_sample(self._client, self._store, self._library, self.snapshots)) is not None:
                self._recorder.samples.append(sample)
                self.last_sample = sample
                self.last_sample_at = datetime.now(UTC)
            self.status.last_error = None
        except (HAError, KeyError, ValueError) as err:
            _LOGGER.warning("Shadow tick failed: %s", err)
            self.status.last_error = str(err)

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
            except Exception as err:  # noqa: BLE001 - logged, the next tick retries
                _LOGGER.warning("Replan failed: %s", err)
                self.status.last_error = str(err)
            if not self._replan_again:
                return

    async def pv_correction(self) -> PvCorrection | None:
        """Correction learned from the shadow data, refreshed hourly."""
        now = datetime.now(UTC)
        if self._correction is None or now - self._correction_at > CORRECTION_REFRESH:
            rows = await asyncio.to_thread(self.shadow.rows, now - LEARNING_WINDOW)
            self._correction = learn(rows, await local_timezone(self._client))
            self._correction_at = now
        return self._correction

    async def _replan(self, start: datetime) -> None:
        result = await current_plan(
            self._client, self._store, self._library, self.snapshots, await self.pv_correction()
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
        }
        await asyncio.to_thread(self.shadow.store_planned, start, planned)
