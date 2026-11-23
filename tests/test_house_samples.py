"""The house model learned from heat_pump_samples (design/heat_pump.md, 14), with synthetic data over several days."""

from datetime import UTC, datetime, timedelta
import math
from pathlib import Path
import random
from typing import Any

import pytest
from sunflow.config.models import DeviceConfig, HouseSettings, Room, SunflowConfig
from sunflow.planning.house_model import (
    HA_HOURLY,
    MIN_DAYS,
    SAMPLES,
    HouseData,
    HouseModel,
    choose_samples,
    fit,
    learn_house,
    sample_hours,
    samples_house_data,
)
from sunflow.shadow.store import ShadowStore

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
FIVE = timedelta(minutes=5)
MIN = timedelta(minutes=1)
ROOM = "sensor.wohnzimmer"
WEIGHTS = {ROOM: 1.0}
PROFILE = "esphome/rotex_hpsu_can"
# The heating surfaces of the synthetic house: underfloor, K = 180 W/K^1.1, spread 2 K + 1 K per kW
K_TRUE, N_TRUE, S0, S1 = 180.0, 1.1, 2.0, 1.0
# Heat loss of the house (W/K) and its room temperature
LOSS_W_PER_K, ROOM_C = 150.0, 21.0


def _outside(moment: datetime) -> float:
    """Outside swings over the day and from day to day."""
    hours = moment.timestamp() / 3600
    return 3 + 4 * math.sin(hours / 24 * 2 * math.pi) + 2 * math.sin(hours / 77 * 2 * math.pi)


def _row(start: datetime, rng: random.Random | None = None, noise: float = 0.0, **changes: Any) -> dict[str, Any]:
    """A 5 minute row of valid heating: the heat the house loses, flow and return from the true curve."""
    outside = _outside(start)
    room = ROOM_C + (rng.gauss(0, 0.05) if rng and noise else 0.0)
    heat = LOSS_W_PER_K * (room - outside)
    water = room + (heat / K_TRUE) ** (1 / N_TRUE)
    spread = S0 + S1 * heat / 1000
    tv, tr = water + spread / 2, water - spread / 2
    if rng and noise:
        tv += rng.gauss(0, noise)
        tr += rng.gauss(0, noise)
        heat *= 1 + rng.gauss(0, noise / 4)
    row = {
        "device_id": "hp", "start": start, "samples": 5, "tv_c": tv, "tr_c": tr, "tvbh_c": tv, "tv_max_c": tv + 0.2,
        "heat_w": heat, "heat_source": "measured", "outside_c": outside, "bypass_pct": 0.0, "setpoint_c": tv + 1,
        "max_setpoint_c": tv + 1, "written_c": None, "compressor": 1.0, "program": "heating", "activity": "heating",
        "strategy": "fixed", "error": "err_0", "rooms": {ROOM: room}, "events": [],
        "flags": {"valid": True, "frozen": [], "after_run": [], "blocked": False, "fault": False, "self_limited": False, "ventilation": []},
    }
    flags = changes.pop("flags", {})
    row.update(changes)
    row["flags"] = row["flags"] | flags
    return row


def _days(days: float, noise: float = 0.0, seed: int = 1, end: datetime = NOW) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    count = int(days * 24 * 12)
    return [_row(end - (count - i) * FIVE, rng, noise) for i in range(count)]



def _house_model(**changes: Any) -> HouseModel:
    values: dict[str, Any] = {"a": 0.02, "b": 0.04, "c": 0.3, "d": 0.02, "days": 14, "samples": 300, "error_k": 0.2, "baseline_k": 0.5} | changes
    return HouseModel(**values)


# House model from heat_pump_samples


def _house_rows(days: int, end: datetime = NOW, seed: int = 2) -> list[dict[str, Any]]:
    """5 minute rows of a 1R1C house: τ 50 h, 25 kWh/K; heating 2 h on, 1 h pause; a hot water hour a day."""
    rng = random.Random(seed)
    rows = []
    inside = 21.0
    count = days * 288
    for i in range(count):
        start = end - (count - i) * FIVE
        outside = _outside(start)
        heating = (i // 12) % 3 != 2
        hot_water = (i // 12) % 24 == 5
        heat = 3600.0 if heating and not hot_water else 0.0
        changes: dict[str, Any] = {"rooms": {ROOM: inside + rng.gauss(0, 0.01)}, "outside_c": outside, "heat_w": heat,
                                   "compressor": 1.0 if heat else 0.0}
        if hot_water:
            changes |= {"activity": "hot_water", "heat_w": 7000.0, "flags": {"valid": False, "frozen": ["activity_hot_water"]}}
        elif not heating:
            changes |= {"activity": "idle", "flags": {"valid": False, "frozen": ["activity_idle"]}}
        rows.append(_row(start, **changes))
        # Per 5 minutes: a = 0.02/h, b = 0.04 K/kWh
        inside += (0.02 * (outside - inside) + 0.04 * heat / 1000) / 12
    return rows


def test_hours_from_samples_leave_out_hot_water_and_ventilation() -> None:
    rows = _house_rows(2)
    rows[30]["flags"] = rows[30]["flags"] | {"ventilation": [ROOM]}

    hours, excluded, source = sample_hours(rows, WEIGHTS)

    assert source == "measured"
    # A hot water hour a day plus the aired one
    assert excluded == 2 + 1
    assert len(hours) == 48 - excluded
    heating_hour = next(v for h, v in hours.items() if v["heat"] and v["heat"] > 3)
    assert heating_hour["heat"] == pytest.approx(3.6)


def test_house_model_learned_from_samples() -> None:
    rows = _house_rows(14)
    ha = HouseData(rows=[], per_room={}, inside={}, heat_source=None, outside_entity="sensor.out", sun_entity=None)
    config = SunflowConfig(house=HouseSettings(rooms=[Room(entity_id=ROOM)]))

    data = samples_house_data(rows, config, ha, coverage_days=12)
    model = fit(data.rows, NOW)

    assert (data.source, data.coverage_days, data.heat_source) == (SAMPLES, 12, "measured")
    assert model is not None and model.useful
    assert model.tau_h == pytest.approx(50, rel=0.1)
    assert model.capacity_kwh_per_k == pytest.approx(25, rel=0.1)


def test_valid_days_of_the_table(tmp_path: Path) -> None:
    store = ShadowStore(tmp_path)
    try:
        rows = _days(3, end=NOW.replace(hour=0))
        # One day aired, a broken row from another version
        for row in rows:
            if row["start"].date() == (NOW - timedelta(days=2)).date():
                row["flags"]["ventilation"] = [ROOM]
        store.store_heat_pump_samples(rows)
        store._db.execute("INSERT INTO heat_pump_samples (device_id, start, samples, flags) VALUES ('hp', '2026-01-01T00:00:00+00:00', 5, 'x')")
        assert store.heat_pump_valid_days("hp", 3600) == 2
        assert store.heat_pump_valid_days("other", 3600) == 0
    finally:
        store.close()


def test_choice_of_the_source() -> None:
    useful, unsure = _house_model(), _house_model(error_k=0.9)

    assert choose_samples(useful, useful, kept=False)
    assert choose_samples(unsure, None, kept=False)
    assert choose_samples(unsure, unsure, kept=False)
    # Not worse in use: a useful model from Home Assistant is not replaced by an unsure one
    assert not choose_samples(unsure, useful, kept=False)
    # Once chosen it stays, unless the samples give no model at all
    assert choose_samples(unsure, useful, kept=True)
    assert not choose_samples(None, useful, kept=True)


class _NoHistory:
    """Home Assistant without any history: the hourly means are empty."""

    async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
        return {}


async def test_house_model_switches_after_seven_days(tmp_path: Path) -> None:
    """Below MIN_DAYS of valid heating Home Assistant's hourly means count; from then on the samples."""
    store = ShadowStore(tmp_path)
    config = SunflowConfig(
        devices=[DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")],
        house=HouseSettings(rooms=[Room(entity_id=ROOM)]),
    )
    config.learning_sources.outdoor_temperature = "sensor.out"
    profiles = {PROFILE: {"capabilities": ["heat_pump"]}}
    try:
        store.store_heat_pump_samples(_house_rows(MIN_DAYS - 1, end=NOW.replace(hour=0)))
        before = await learn_house(_NoHistory(), config, profiles, NOW, None, store)  # type: ignore[arg-type]
        store.store_heat_pump_samples(_house_rows(14, end=NOW.replace(hour=0)))
        after = await learn_house(_NoHistory(), config, profiles, NOW, None, store)  # type: ignore[arg-type]
    finally:
        store.close()

    assert (before.data.source, before.data.coverage_days, before.model) == (HA_HOURLY, MIN_DAYS - 1, None)
    assert (after.data.source, after.data.coverage_days) == (SAMPLES, 14)
    assert after.model is not None and after.model.useful


# Sun of each hour when learning the house from heat_pump_samples (14)

SUN = "sensor.lux"
# K per hour at full sun of the synthetic house
SUN_GAIN = 0.3


def _sun(hour: datetime) -> float:
    """Brightness (lux) of an hour: a day arc from 6 to 18 UTC, brighter on some days than others."""
    position = (hour.hour + 0.5 - 6) / 12
    day = 0.6 + 0.4 * math.sin(hour.timestamp() / 86400 / 3 * 2 * math.pi)
    return 50_000 * day * math.sin(position * math.pi) if 0 < position < 1 else 0.0


def _sunny_house_rows(days: int, end: datetime) -> list[dict[str, Any]]:
    """Like _house_rows, the sun warming the house by SUN_GAIN K/h at full sun (relative to the brightest hours)."""
    rows = _house_rows(days, end=end)
    bright = sorted(_sun(r["start"].replace(minute=0)) for r in rows)[int(0.95 * (len(rows) - 1))]
    inside = rows[0]["rooms"][ROOM]
    for row in rows:
        row["rooms"] = {ROOM: inside}
        sun = min(_sun(row["start"].replace(minute=0)) / bright, 1.0)
        inside += (0.02 * (row["outside_c"] - inside) + 0.04 * row["heat_w"] / 1000 * (row["activity"] == "heating") + SUN_GAIN * sun) / 12
    return rows


class _SunHistory:
    """Home Assistant with the hourly statistics of the sun sensor only; its current state is something else."""

    def __init__(self, hours: list[datetime]) -> None:
        self.hours = hours

    async def command(self, msg_type: str, **data: Any) -> dict[str, Any]:
        if msg_type == "recorder/statistics_during_period" and data["statistic_ids"] == [SUN]:
            return {SUN: [{"start": h.timestamp() * 1000, "mean": _sun(h)} for h in self.hours]}
        return {}

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        return {"entity_id": entity_id, "state": "99999"}


async def test_house_from_samples_takes_the_sun_of_each_hour(tmp_path: Path) -> None:
    """The sun comes from the sensor's mean of that very hour - also for hours without Home Assistant rows."""
    store = ShadowStore(tmp_path)
    config = SunflowConfig(
        devices=[DeviceConfig(id="hp", ha_device_id="dev", profile=PROFILE, profile_version=2, name="HPSU CAN")],
        house=HouseSettings(rooms=[Room(entity_id=ROOM)]),
    )
    config.learning_sources.outdoor_temperature = "sensor.out"
    config.learning_sources.irradiance = SUN
    profiles = {PROFILE: {"capabilities": ["heat_pump"]}}
    end = NOW.replace(hour=0)
    rows = _sunny_house_rows(21, end)
    hours = sorted({r["start"].replace(minute=0) for r in rows})
    try:
        store.store_heat_pump_samples(rows)
        # Home Assistant has no room or outside history: none of its hourly rows exists
        learned = await learn_house(_SunHistory(hours), config, profiles, end, None, store)  # type: ignore[arg-type]
    finally:
        store.close()

    data, model = learned.data, learned.model
    assert data.source == SAMPLES
    bright = data.sun_bright
    assert bright is not None
    for row in data.rows:
        assert row.sun == pytest.approx(min(_sun(row.start) / bright, 1.0))
    assert model is not None and model.c is not None
    assert model.c == pytest.approx(SUN_GAIN, rel=0.15)
