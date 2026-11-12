"""Background loop: periodic planning and shadow recording."""

import asyncio
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, tzinfo
import json
import logging
from typing import Any
from zoneinfo import ZoneInfo

from sunflow import i18n
from sunflow.analysis.history import level_key
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.control.charger import ChargerWatch
from sunflow.control.controller import PlanController
from sunflow.devices.discovery import Snapshot, SnapshotCache, fetch_snapshot
from sunflow.devices.profiles import ProfileLibrary, controls_battery, planned_battery
from sunflow.devices.resolver import preview
from sunflow.devices.site import (
    device_parameters,
    measurement_device,
    pv_strings,
    site_status,
)
from sunflow.ha import HAClient, HAError
from sunflow.planning.load_forecast import HISTORY_DAYS
from sunflow.planning.load_model import LoadModel, fit as fit_load_model
from sunflow.planning.plan import (
    SLOT,
    SLOT_HOURS,
    PlanResult,
    compute_plan,
    weather_entity,
)
from sunflow.planning.pv_episodes import SoilingHistory
from sunflow.planning.pv_learning import PvCorrection, cloud_class, learn, learn_planes
from sunflow.planning.pv_season import (
    PlaneSeason,
    compare,
    day_levels,
    season_profile,
    snow_ahead,
)
from sunflow.planning.pv_steps import Expected, PlaneState, daily_energy, plane_states
from sunflow.planning.pv_sun import (
    PlaneSun,
    SunCorrection,
    learn_planes as learn_sun_planes,
    learned_expectation,
    refresh_levels,
    snow_now,
)
from sunflow.planning.vehicle_soc import estimator
from sunflow.planning.vehicles import recognize, soc_of, unplugged_choices
from sunflow.shadow.counters import EnergyCounters

from .store import (
    PLAN_AHEAD,
    Sample,
    ShadowStore,
    SlotRecorder,
    actual_action,
    slot_start,
)

_LOGGER = logging.getLogger(__name__)

SAMPLE_SECONDS = 60


async def local_timezone(client: HAClient) -> ZoneInfo:
    """Return Home Assistant's configured time zone."""
    config = await client.command("get_config")
    return ZoneInfo(config["time_zone"])


# PV correction: learned from this many days of shadow data, refreshed this often
LEARNING_WINDOW = timedelta(days=120)
# Live values older than this are not used for control
SAMPLE_MAX_AGE = timedelta(minutes=3)
CORRECTION_REFRESH = timedelta(hours=1)
# The correction by sun position learns from up to two years of history: once a day
SUN_REFRESH = timedelta(hours=24)
# After a failed run (e.g. Home Assistant away at the start)
SUN_RETRY = timedelta(minutes=15)
# The state of the modules (leaves blown away by the wind) from the last days: more often
SUN_LEVEL_REFRESH = timedelta(hours=3)
# A failed replan runs every query again; not every minute while Home Assistant has a
# problem. The pause doubles with each failure up to the maximum
REPLAN_RETRY = timedelta(minutes=5)
REPLAN_RETRY_MAX = timedelta(minutes=30)
# Seconds a plan request waits for a replan that is running after a change
FRESH_PLAN_WAIT = 20.0
# Live values deviating this much from the running slot replan before the next slot
EARLY_REPLAN_PV_W = 800.0
EARLY_REPLAN_PV_SHARE = 0.3
EARLY_REPLAN_LOAD_W = 1500.0
EARLY_REPLAN_SOC_SHARE = 0.05
# Not more often than this; the deviation has to last a few minutes (clouds pass)
EARLY_REPLAN_PAUSE = timedelta(minutes=3)
EARLY_REPLAN_SAMPLES = 3
RECENT_SAMPLES = 5
# Fewer samples (just after a start) say nothing about what the battery does
RECENT_ACTION_MIN_SAMPLES = 3
NOWCAST_MIN_SAMPLES = 2
# How the day went so far: measured against raw forecast PV over this time
PV_RECENT = timedelta(hours=3)


async def current_plan(
    client: HAClient,
    store: ConfigStore,
    library: ProfileLibrary,
    snapshots: SnapshotCache | None = None,
    pv_correction: PvCorrection | None = None,
    load_model: LoadModel | None = None,
    plane_corrections: dict[str, PvCorrection] | None = None,
    live_pv_w: float | None = None,
    live_load_w: float | None = None,
    consumer_hours: list[tuple[datetime, float]] | None = None,
    vehicle_hours: dict[str, list[tuple[datetime, float]]] | None = None,
    chargers: ChargerWatch | None = None,
    sun_corrections: dict[str, SunCorrection] | None = None,
    pv_recent: tuple[float, float, float] | None = None,
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
        live_pv_w,
        live_load_w,
        consumer_hours,
        vehicle_hours,
        chargers,
        sun_corrections,
        pv_recent,
    )


def expected_pv_w(
    row: dict[str, Any],
    tz: tzinfo,
    site: PvCorrection | None,
    own: dict[str, PvCorrection],
    sun: dict[str, SunCorrection],
    location: tuple[float, float] | None,
) -> float:
    """What the plan expects for a recorded slot's raw forecast: each plane with the correction the plan uses."""
    start = datetime.fromisoformat(row["start"])
    hour, kind = start.astimezone(tz).hour, cloud_class(row.get("weather_cloud"))
    site_factor = site.factor(hour, kind) if site is not None and site.slots else 1.0
    planes = row.get("plane_forecasts") or {}
    if not planes:
        return row["forecast_pv_w"] * site_factor
    total = 0.0
    for key, power in planes.items():
        if key in sun and location is not None:
            factor = sun[key].factor_at(start + SLOT / 2, power, *location)
        elif key in own and own[key].slots:
            factor = own[key].factor(hour, kind)
        else:
            factor = site_factor
        total += power * factor
    return total


async def take_sample(
    client: HAClient, store: ConfigStore, library: ProfileLibrary, snapshots: SnapshotCache | None = None
) -> Sample | None:
    """Read SoC, battery, grid and load of the battery inverter and the site PV."""
    config = store.load()
    profiles = library.profiles
    device = planned_battery(config.devices, profiles)
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

    consumers = consumer_powers(config, profiles, values)
    # Also without a battery: the cars' SoC between their reports
    estimator.observe(config.vehicles, snapshot.states, vehicle_charging(config, profiles, snapshot, consumers), datetime.now(UTC))
    if device is None:
        return None

    socs: dict[str, float] = {}
    powers: dict[str, float] = {}
    for battery in [d for d in config.devices if controls_battery(profiles[d.profile])]:
        roles = values(battery)
        params = {
            p["name"]: p["effective"]
            for p in device_parameters(profiles[battery.profile], battery, snapshot, config.battery_modules.get(battery.id))
        }
        if (capacity := params.get("capacity_wh")) and (soc := roles.get("battery_soc")) is not None:
            socs[battery.id] = capacity * soc / 100
        if (power := roles.get("battery_power")) is not None:
            powers[battery.id] = power
    return Sample(
        # All batteries as one, like the plan; unknown while the first one's SoC is
        soc_wh=sum(socs.values()) if device.id in socs else None,
        battery_w=sum(powers.values()) if device.id in powers else None,
        grid_w=values(measurement_device(config, profiles, "grid")).get("grid_power"),
        pv_w=site_status(config.devices, profiles, snapshot, measured_by=config.measured_by).pv_total,
        load_w=values(measurement_device(config, profiles, "load")).get("load_power"),
        loadpoint_w=sum(consumers.values()) if consumers else None,
        consumers=consumers,
        levels=consumer_levels(config, profiles, snapshot),
        strings=string_powers(config, profiles, snapshot),
        battery_socs=socs,
        battery_powers=powers,
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


def vehicle_charging(
    config: SunflowConfig, profiles: dict[str, Any], snapshot: Any, consumers: dict[str, float]
) -> dict[str, float]:
    """What each car is charged with now (W by vehicle id), from the wallbox that recognized it."""
    charging: dict[str, float] = {}
    for device in config.devices:
        if not config.vehicles_at(device.id) or not consumers.get(device.id):
            continue
        roles = {
            r.role: r
            for r in preview(
                profiles[device.profile], snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()}
            )
        }
        if (vehicle := recognize(config, device, roles, snapshot.states).vehicle) is not None:
            charging[vehicle.id] = charging.get(vehicle.id, 0.0) + consumers[device.id]
    return charging


def consumer_levels(config: SunflowConfig, profiles: dict[str, Any], snapshot: Any) -> dict[str, float]:
    """Levels by key: every car's SoC (also away), tank temperatures and the SoC of wallboxes without cars."""
    levels = {level_key(v.id): soc for v in config.vehicles if (soc := soc_of(v, snapshot.states)) is not None}
    for device in config.devices:
        profile = profiles[device.profile]
        if ("loadpoint" not in profile["capabilities"] and "load" not in profile["capabilities"]) or config.vehicles_at(device.id):
            continue
        roles = {
            r.role: r
            for r in preview(profile, snapshot, device.ha_device_id, {k: o.model_dump() for k, o in device.role_overrides.items()})
        }
        level = next(
            (roles[r].value for r in ("vehicle_soc", "temperature") if r in roles and isinstance(roles[r].value, int | float)), None
        )
        if level is not None:
            levels[device.id] = level
    return levels


def consumer_powers(config: SunflowConfig, profiles: dict[str, Any], values: Any) -> dict[str, float]:
    """Power of every controllable consumer by device id."""
    powers = {}
    for device in config.devices:
        capabilities = profiles[device.profile]["capabilities"]
        if "loadpoint" in capabilities or "load" in capabilities:
            readings = values(device)
            power = readings.get("charge_power") if "loadpoint" in capabilities else readings.get("power")
            powers[device.id] = max(power or 0.0, 0.0)
    return powers


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
        # Planes that changed suddenly against the others (leaves, cleaning), by key
        self.plane_states: dict[str, PlaneState] = {}
        # PV correction by sun position per plane, learned in the background
        self.sun_planes: list[PlaneSun] = []
        self.soiling = SoilingHistory()
        # Soiling to expect by season per plane, compared in shadow mode
        self.seasons: dict[str, PlaneSeason] = {}
        # Snow on all planes over the last days (shadow mode: shown, not planned with)
        self.snow: dict[str, Any] | None = None
        # Snow the weather forecast expects in the next days
        self.snow_ahead: dict[str, Any] | None = None
        self._location: tuple[float, float] | None = None
        self.sun_learned_at: datetime | None = None
        self._sun_level_at: datetime | None = None
        self.sun_error: str | None = None
        self._sun_task: asyncio.Task[None] | None = None
        # Energy per PV string for Home Assistant (set by the app)
        self.counters: EnergyCounters | None = None
        self._correction_at = datetime.min.replace(tzinfo=UTC)
        self._recorder: SlotRecorder | None = None
        self._replan_task: asyncio.Task[None] | None = None
        # Slot of the last successful plan; a failed replan is retried on the next tick
        self._planned_slot: datetime | None = None
        # A change arrived while a replan was running: plan once more afterwards
        self._replan_again = False
        self._replan_retry_at = datetime.min.replace(tzinfo=UTC)
        # The last minutes, for the nowcast and to notice deviations from the plan
        self._recent: deque[Sample] = deque(maxlen=RECENT_SAMPLES)
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
            try:
                await self._finish_slot()
            except Exception:
                # E.g. the database full or locked: this slot is lost, measuring and control go on
                _LOGGER.exception("Storing the finished slot failed")
            self._recorder = SlotRecorder(start=start)
        # Measuring goes on while planning fails, so the shadow data has no holes
        if self._planned_slot != start and now >= self._replan_retry_at:
            try:
                await self._replan(start)
                self.status.last_error = None
                self._replan_failures = 0
            except Exception as err:
                # Whatever fails in planning: the control step below still runs and falls back on a stale plan
                pause = min(REPLAN_RETRY * 2**self._replan_failures, REPLAN_RETRY_MAX)
                self._replan_failures += 1
                _LOGGER.warning(
                    "Replan failed, retrying in %s: %s", pause, err, exc_info=not isinstance(err, (HAError, KeyError, ValueError))
                )
                self.status.last_error = f"Plan: {err}"
                self._replan_retry_at = now + pause
        try:
            if (sample := await take_sample(self._client, self._store, self._library, self.snapshots)) is not None:
                self._recorder.samples.append(sample)
                self._recent.append(sample)
                self.last_sample = sample
                self.last_sample_at = datetime.now(UTC)
                if self.counters is not None and sample.strings:
                    self.counters.add(sample.strings, self.last_sample_at)
        except Exception as err:
            _LOGGER.warning("Sample failed: %s", err, exc_info=not isinstance(err, (HAError, KeyError, ValueError)))
        if (why := self._deviation()) is not None and now >= self._replan_retry_at:
            _LOGGER.info("Replanning early: %s", why)
            try:
                await self._replan(start)
            except Exception as err:
                _LOGGER.warning("Early replan failed: %s", err, exc_info=not isinstance(err, (HAError, KeyError, ValueError)))
        if self.snapshots is not None:
            try:
                snapshot = await self.snapshots.get()
                self._end_vehicle_choices(snapshot)
                if self.controller is not None:
                    # Old live values (samples failing) would steer by what was, not what is
                    fresh = self.last_sample_at is not None and now - self.last_sample_at <= SAMPLE_MAX_AGE
                    await self.controller.tick(
                        self._store.load(), self.latest, self.status.last_plan, snapshot, now, self.last_sample if fresh else None
                    )
                    # A charger became ready (test) or full: the plan follows right away
                    if self.controller.chargers.changed:
                        self.controller.chargers.changed = False
                        self.request_replan()
            except Exception as err:
                _LOGGER.warning("Control step failed: %s", err, exc_info=not isinstance(err, HAError))

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

    def live_pv(self) -> float | None:
        """Mean PV of the last minutes."""
        values = [s.pv_w for s in self._recent if s.pv_w is not None]
        # A single reading after a restart is chance (a passing cloud)
        return sum(values) / len(values) if len(values) >= NOWCAST_MIN_SAMPLES else None

    def live_load(self) -> float | None:
        """Mean consumption of the last minutes without the controllable consumers (as forecast)."""
        values = [s.load_w - (s.loadpoint_w or 0.0) for s in self._recent if s.load_w is not None]
        return max(sum(values) / len(values), 0.0) if len(values) >= NOWCAST_MIN_SAMPLES else None

    def _deviation(self) -> str | None:
        """Why the plan no longer fits the live values (lasting the last minutes), None if it does."""
        if self.latest is None or self.status.last_plan is None or len(self._recent) < EARLY_REPLAN_SAMPLES:
            return None
        if datetime.now(UTC) - self.status.last_plan < EARLY_REPLAN_PAUSE:
            return None
        slot = next(
            (s for s in self.latest.plan.slots if s.start <= datetime.now(UTC) < s.end),
            None,
        )
        if slot is None:
            return None

        def lasting(value: Callable[[Sample], float | None], limit: float) -> float | None:
            deviations = [d for s in list(self._recent)[-EARLY_REPLAN_SAMPLES:] if (d := value(s)) is not None]
            if len(deviations) < EARLY_REPLAN_SAMPLES or not all(abs(d) > limit for d in deviations):
                return None
            # The same direction throughout, not a flicker around the plan
            return sum(deviations) / len(deviations) if all(d > 0 for d in deviations) or all(d < 0 for d in deviations) else None

        pv_limit = max(EARLY_REPLAN_PV_W, EARLY_REPLAN_PV_SHARE * slot.pv_w)
        if (d := lasting(lambda s: None if s.pv_w is None else s.pv_w - slot.pv_w, pv_limit)) is not None:
            return f"PV {d:+.0f} W gegenüber Plan"
        planned_load = slot.load_w + sum((slot.flex_w or {}).values())
        if (d := lasting(lambda s: None if s.load_w is None else s.load_w - planned_load, EARLY_REPLAN_LOAD_W)) is not None:
            return f"Verbrauch {d:+.0f} W gegenüber Plan"
        capacity = self.latest.battery.capacity_wh if self.latest.battery else None
        if capacity and (d := lasting(lambda s: None if s.soc_wh is None else s.soc_wh - slot.soc_wh, EARLY_REPLAN_SOC_SHARE * capacity)) is not None:
            return f"Ladestand {d:+.0f} Wh gegenüber Plan"
        return None

    async def _finish_slot(self) -> None:
        if self._recorder is None or not self._recorder.samples:
            return
        await asyncio.to_thread(self.shadow.store_actual, self._recorder.start, self._recorder.summary())

    def own_hours(self) -> tuple[list[tuple[datetime, float]], dict[str, list[tuple[datetime, float]]]]:
        """From the own samples of the history window: hourly power of all controllable consumers, hourly SoC per car."""
        hours: dict[datetime, list[float]] = {}
        socs: dict[str, dict[datetime, list[float]]] = {}
        prefix = level_key("")
        for row in self.shadow.rows(datetime.now(UTC) - timedelta(days=HISTORY_DAYS)):
            hour = datetime.fromisoformat(row["start"]).replace(minute=0)
            if row.get("actual_loadpoint_w") is not None:
                hours.setdefault(hour, []).append(row["actual_loadpoint_w"])
            for key, value in (row.get("actual_levels") or {}).items():
                if key.startswith(prefix):
                    socs.setdefault(key.removeprefix(prefix), {}).setdefault(hour, []).append(value)
        return (
            [(hour, sum(values) / len(values)) for hour, values in sorted(hours.items())],
            {car: [(hour, sum(v) / len(v)) for hour, v in sorted(by_hour.items())] for car, by_hour in socs.items()},
        )

    def request_replan(self) -> None:
        """Replan right away (after a change in the control tab), in the background."""
        if self._replan_task is not None and not self._replan_task.done():
            self._replan_again = True
            return
        self._replan_task = asyncio.create_task(self._replan_now())

    async def replan_and_wait(self) -> PlanResult | None:
        """Compute a new plan now, the same way as every quarter hour, and return it."""
        self.request_replan()
        if self._replan_task is not None:
            await asyncio.shield(self._replan_task)
        return self.latest

    async def ensure_plan(self) -> PlanResult | None:
        """Return the latest plan, computing one (shared with other callers) if there is none.

        While a requested replan runs (a setting was just changed) it waits for it, so a view
        opened right after the change does not show the plan from before.
        """
        if self.latest is None:
            self.request_replan()
        if self._replan_task is not None and not self._replan_task.done():
            if self.latest is None:
                await asyncio.shield(self._replan_task)
            else:
                # Not forever: a slow Home Assistant still gets the older plan
                await asyncio.wait({self._replan_task}, timeout=FRESH_PLAN_WAIT)
        return self.latest

    async def _replan_now(self) -> None:
        while True:
            self._replan_again = False
            if not self._client.connected:
                # Home Assistant restarting: the tick plans again once connected
                _LOGGER.info("Home Assistant not connected (restarting?) – planning again once it is back")
                self._planned_slot = None
                return
            try:
                await self._replan(slot_start(datetime.now(UTC)))
                self.status.last_error = None
            except Exception as err:
                _LOGGER.warning("Replan failed: %s", err)
                self.status.last_error = f"Plan: {err}"
            if not self._replan_again:
                return

    def recent_means(self) -> tuple[float, float] | None:
        """Mean grid and battery power of the last minutes, None while too few samples.

        One sample combines meters read at different moments (e.g. the grid meter every
        few seconds, the inverter every half minute) and catches load steps the inverter
        is still regulating; it often looks like grid charging that is not there.
        """
        pairs = [(s.grid_w, s.battery_w) for s in self._recent if s.grid_w is not None and s.battery_w is not None]
        if len(pairs) < RECENT_ACTION_MIN_SAMPLES:
            return None
        return sum(g for g, _ in pairs) / len(pairs), sum(b for _, b in pairs) / len(pairs)

    def recent_losses(self) -> float | None:
        """Mean losses of inverter and battery over the last minutes (W): the balance minus the measured load."""
        gaps = [
            s.pv_w + s.grid_w - s.battery_w - s.load_w
            for s in self._recent
            if None not in (s.pv_w, s.grid_w, s.battery_w, s.load_w)
        ]
        if len(gaps) < RECENT_ACTION_MIN_SAMPLES:
            return None
        return max(sum(gaps) / len(gaps), 0.0)

    def recent_action(self) -> str | None:
        """What the battery did over the last minutes."""
        return actual_action(*means) if (means := self.recent_means()) else None

    def relearn(self) -> None:
        """Learn again with the next plan instead of within the hour."""
        self._correction_at = datetime.min.replace(tzinfo=UTC)

    @property
    def sun_learning(self) -> bool:
        """The correction by sun position is being learned."""
        return self._sun_task is not None and not self._sun_task.done()

    def learn_sun(self, force: bool = False) -> None:
        """Learn the correction by sun position in the background when it is a day old (or forced)."""
        now = datetime.now(UTC)
        stale = self.sun_learned_at is None or now - self.sun_learned_at > SUN_REFRESH
        if self.sun_learning:
            return
        if force or stale:
            self._sun_task = asyncio.create_task(self._learn_sun())
        elif self.sun_planes and (self._sun_level_at is None or now - self._sun_level_at > SUN_LEVEL_REFRESH):
            self._sun_task = asyncio.create_task(self._refresh_sun_levels())

    async def _refresh_sun_levels(self) -> None:
        try:
            snapshot = await self.snapshots.get() if self.snapshots else await fetch_snapshot(self._client)
            config = self._store.load()
            tz = await local_timezone(self._client)
            await refresh_levels(self._client, config, self._library.profiles, snapshot, self.sun_planes, tz)
            self.snow = await snow_now(self._client, config, snapshot, self.sun_planes, tz)
            self.snow_ahead = await self._snow_ahead(config, snapshot)
        except (HAError, KeyError, TypeError, ValueError) as err:
            _LOGGER.warning("Updating the state of the PV planes failed: %s", err)
        self._sun_level_at = datetime.now(UTC)

    async def _learn_sun(self) -> None:
        try:
            snapshot = await self.snapshots.get() if self.snapshots else await fetch_snapshot(self._client)
            config = self._store.load()
            tz = await local_timezone(self._client)
            self.sun_planes, self.soiling = await learn_sun_planes(
                self._client, config, self._library.profiles, snapshot, tz, self.plane_states
            )
            self.seasons = season_profile(self.soiling, datetime.now(tz).date())
            location = await self._client.command("get_config")
            self._location = (location["latitude"], location["longitude"])
            # The planes' states then compare with what each plane has learned to deliver
            self.relearn()
            self.snow = await snow_now(self._client, config, snapshot, self.sun_planes, tz)
            self.snow_ahead = await self._snow_ahead(config, snapshot)
            self.sun_error = None
            self._sun_level_at = datetime.now(UTC)
            self.sun_learned_at = datetime.now(UTC)
        except Exception as err:
            _LOGGER.warning("Learning PV by sun position failed: %s", err, exc_info=not isinstance(err, HAError))
            self.sun_error = str(err)
            # Tried again in a while, not with every plan and not only tomorrow
            self.sun_learned_at = datetime.now(UTC) - SUN_REFRESH + SUN_RETRY
        if self._store.load().optimizer.pv_sun:
            self.request_replan()

    async def _snow_ahead(self, config: SunflowConfig, snapshot: Snapshot) -> dict[str, Any] | None:
        """Snow in the hourly forecast of the weather entity; None without one."""
        if (entity_id := weather_entity(config, snapshot)) is None:
            return None
        try:
            response = await self._client.call_service(
                "weather", "get_forecasts", {"type": "hourly"}, target={"entity_id": entity_id}, return_response=True
            )
            return snow_ahead(response["response"][entity_id]["forecast"])
        except (HAError, KeyError, TypeError):
            return None

    def pv_recent(self, tz: tzinfo) -> tuple[float, float, float] | None:
        """Measured, raw forecast and expected PV energy (Wh) of the last hours, for how the day goes."""
        rows = self.shadow.rows(datetime.now(UTC) - PV_RECENT)
        rows = [r for r in rows if r["actual_pv_w"] is not None and r["forecast_pv_w"] is not None]
        if not rows:
            return None
        optimizer = self._store.load().optimizer
        learning = optimizer.pv_learning
        expected = [
            expected_pv_w(
                row, tz,
                self._correction if learning else None,
                self.plane_corrections if learning else {},
                self.sun_corrections() if optimizer.pv_sun else {},
                self._location,
            )
            for row in rows
        ]
        return (
            sum(max(r["actual_pv_w"], 0.0) for r in rows) * SLOT_HOURS,
            sum(r["forecast_pv_w"] for r in rows) * SLOT_HOURS,
            sum(expected) * SLOT_HOURS,
        )

    def sun_corrections(self) -> dict[str, SunCorrection]:
        """The learned corrections good enough to plan with, by plane key."""
        return {p.key: p.correction for p in self.sun_planes if p.plannable and p.correction is not None}

    async def pv_correction(self) -> PvCorrection | None:
        """Correction learned from the shadow data, refreshed hourly."""
        now = datetime.now(UTC)
        if self._correction is None or now - self._correction_at > CORRECTION_REFRESH:
            rows = await asyncio.to_thread(self.shadow.rows, now - LEARNING_WINDOW)
            tz = await local_timezone(self._client)
            self._correction = learn(rows, tz)
            config = self._store.load()
            strings = {plane.key: plane.strings for plane in config.forecast_planes if plane.strings}
            self.plane_states = plane_states(rows, strings, tz, self._plane_expected())
            self._store_soiling_ahead(tz)
            self.plane_corrections = learn_planes(
                rows,
                strings,
                tz,
                {plane.key: plane.learn_since for plane in config.forecast_planes if plane.learn_since},
                self.plane_states,
            )
            self.load_model = await asyncio.to_thread(
                fit_load_model, rows, tz, self._store.load().optimizer.large_load_threshold_w
            )
            self._correction_at = now
        return self._correction

    def _plane_expected(self) -> Expected | None:
        """The forecast per plane as its history corrects it; None until learned."""
        return learned_expectation(self.sun_planes, *self._location) if self._location is not None else None

    def _store_soiling_ahead(self, tz: tzinfo) -> None:
        """Keep tomorrow's expected share per plane, by season and as now, to compare once it is measured."""
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        for key, season in self.seasons.items():
            current, cleaned = self.soiling_now(key)
            self.shadow.store_soiling_ahead(tomorrow, key, season.expected(tomorrow, current, cleaned), current)

    def soiling_now(self, key: str) -> tuple[float, date | None]:
        """The plane's current share of its clean yield and its latest cleaning, from the recorded days."""
        state = self.plane_states.get(key)
        if state is None:
            return 1.0, None
        cleanings = [date.fromisoformat(c["since"]) for c in state.changes if c["change"] > 1]
        return state.level, max(cleanings, default=None)

    async def soiling_check(self) -> dict[str, dict[str, Any] | None]:
        """Per plane: how close the season expectation and keeping the current level came to the measured share."""
        tz = await local_timezone(self._client)
        since = datetime.now(tz).date() - timedelta(days=LEARNING_WINDOW.days)
        stored = await asyncio.to_thread(self.shadow.soiling_ahead, since)
        if not stored:
            return {}
        rows = await asyncio.to_thread(self.shadow.rows, datetime.now(UTC) - LEARNING_WINDOW)
        strings = {plane.key: plane.strings for plane in self._store.load().forecast_planes if plane.strings}
        expected = self._plane_expected()
        energy = daily_energy(rows, strings, tz, expected)
        states = plane_states(rows, strings, tz, expected)
        return {
            key: compare(expected, day_levels(energy, key, states.get(key) or PlaneState()))
            for key, expected in stored.items()
        }

    async def _replan(self, start: datetime) -> None:
        await i18n.update_language(self._client, self._store.load().display.language)
        correction = await self.pv_correction()
        self.learn_sun()
        result = await current_plan(
            self._client,
            self._store,
            self._library,
            self.snapshots,
            correction,
            self.load_model,
            self.plane_corrections,
            self.live_pv(),
            self.live_load(),
            *await asyncio.to_thread(self.own_hours),
            chargers=self.controller.chargers if self.controller is not None else None,
            sun_corrections=self.sun_corrections(),
            pv_recent=await asyncio.to_thread(self.pv_recent, await local_timezone(self._client)),
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
            "batteries": json.dumps(
                {key: {"soc_wh": share.soc_wh, "action": share.action} for key, share in first.batteries.items()}
            ),
        }
        await asyncio.to_thread(self.shadow.store_planned, start, planned)
        ahead = next((slot for slot in result.plan.slots if slot.start == start + PLAN_AHEAD), None)
        if ahead is not None:
            await asyncio.to_thread(
                self.shadow.store_ahead,
                ahead.start,
                ahead.soc_wh,
                {key: share.soc_wh for key, share in ahead.batteries.items()},
                ahead.pv_w,
                ahead.load_w,
            )
