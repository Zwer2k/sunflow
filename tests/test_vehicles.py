"""Tests for recognizing the vehicle at a wallbox."""

from types import SimpleNamespace

import pytest
from sunflow.config.models import (
    ChargingProfile,
    DeviceConfig,
    SunflowConfig,
    Vehicle,
    VehicleDetection,
)
from sunflow.planning.vehicles import recognize, scanned_card

FIAT = Vehicle(id="fiat", name="Fiat 500e", capacity_wh=37500, plugged_entity="binary_sensor.fiat_plugged")
TESLA = Vehicle(id="tesla", name="Model 3", capacity_wh=60000, plugged_entity="binary_sensor.tesla_plugged")
# The card belongs to the vehicle at this wallbox, not to the vehicle itself
CARDS = {"fiat": 1, "tesla": 2}


def _config(vehicles: list[Vehicle], detection: VehicleDetection | None = None, chosen: str | None = None) -> SunflowConfig:
    wallbox = DeviceConfig(id="wb", ha_device_id="w", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    return SunflowConfig(
        devices=[wallbox],
        vehicles=vehicles,
        charging=[ChargingProfile(vehicle_id=v.id, wallbox_id="wb", card=CARDS[v.id]) for v in vehicles],
        vehicle_detection={"wb": detection} if detection else {},
        selected_vehicles={"wb": chosen} if chosen else {},
    )


def _roles(transaction: str = "none") -> dict:
    return {"transaction": SimpleNamespace(raw_state=transaction)}


@pytest.mark.parametrize(
    ("config", "transaction", "states", "expected"),
    [
        pytest.param(_config([FIAT, TESLA]), "card_1", {}, ("tesla", "card"), id="card"),
        pytest.param(_config([FIAT, TESLA], chosen="fiat"), "none", {}, ("fiat", "manual"), id="manual"),
        pytest.param(_config([FIAT, TESLA]), "none", {"binary_sensor.tesla_plugged": {"state": "on"}}, ("tesla", "sensor"), id="sensor"),
        pytest.param(_config([FIAT]), "none", {}, ("fiat", "single"), id="single"),
        pytest.param(_config([FIAT, TESLA]), "none", {}, (None, None), id="unknown"),
        # A card scanned that belongs to nobody here: a foreign car, even with one assigned vehicle
        pytest.param(_config([FIAT]), "card_5", {}, (None, None), id="foreign-card"),
        pytest.param(_config([FIAT], VehicleDetection(single=False)), "none", {}, (None, None), id="single-off"),
        # Card wins over a stale choice in the app
        pytest.param(_config([FIAT, TESLA], chosen="fiat"), "card_1", {}, ("tesla", "card"), id="card-before-choice"),
    ],
)
def test_recognize(config: SunflowConfig, transaction: str, states: dict, expected: tuple) -> None:
    """Card, choice in the app, the car's sensor and the only vehicle, in this order."""
    found = recognize(config, config.devices[0], _roles(transaction), states)

    assert (found.vehicle.id if found.vehicle else None, found.method) == expected


def test_scanned_card() -> None:
    """The go-e counts cards from 0."""
    assert scanned_card("card_0") == 1
    assert scanned_card("without_card") is None
    assert scanned_card(None) is None


def test_flex_loads_plan_the_recognized_vehicle(library) -> None:
    """A go-e wallbox with an assigned car: its need comes from the car's SoC and charging profile."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from sunflow.config.models import FlexSettings
    from sunflow.devices.discovery import Snapshot
    from sunflow.planning.plan import flex_loads

    entities = [
        {"entity_id": "binary_sensor.goe_car", "unique_id": "216505-binary_sensor-car-1", "device_id": "goe"},
        {"entity_id": "sensor.goe_power", "unique_id": "216505-sensor-nrg-11", "device_id": "goe"},
        {"entity_id": "select.goe_frc", "unique_id": "216505-select-frc-frc", "device_id": "goe"},
        {"entity_id": "number.goe_amp", "unique_id": "216505-number-amp-0", "device_id": "goe"},
    ]
    states = {
        "binary_sensor.goe_car": {"state": "on", "attributes": {}},
        "sensor.goe_power": {"state": "0", "attributes": {"unit_of_measurement": "W"}},
        "sensor.fiat_soc": {"state": "40", "attributes": {"unit_of_measurement": "%"}},
    }
    snapshot = Snapshot(entries=[], devices=[{"id": "goe"}], entities=entities, states=states, energy_prefs=None)
    wallbox = DeviceConfig(id="wb", ha_device_id="goe", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    fiat = Vehicle(id="fiat", name="Fiat", capacity_wh=37500, charge_efficiency_pct=90, soc_entity="sensor.fiat_soc")
    config = SunflowConfig(
        devices=[wallbox],
        vehicles=[fiat],
        charging=[ChargingProfile(vehicle_id="fiat", wallbox_id="wb", flex=FlexSettings(target=80))],
    )
    start = datetime(2026, 10, 30, 12, tzinfo=UTC)

    loads, infos, _, _ = flex_loads(config, library.profiles, snapshot, [start + timedelta(minutes=15 * i) for i in range(96)], ZoneInfo("Europe/Berlin"))

    (info,) = infos
    assert (info.vehicle, info.vehicle_method, info.current, info.target) == ("Fiat", "single", 40.0, 80.0)
    # 40 % of 37.5 kWh at 90 % charging efficiency
    assert loads[0].energy_wh == pytest.approx(0.4 * 37500 / 0.9)


def test_flex_loads_plan_an_own_heating_rod(library) -> None:
    """A heating rod added by hand: its need comes from the tank temperature and volume."""
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo

    from sunflow.config.models import RoleOverride
    from sunflow.devices.discovery import Snapshot
    from sunflow.planning.plan import WATER_WH_PER_LITRE_KELVIN, flex_loads

    states = {
        "select.heizstab": {"state": "off", "attributes": {"options": ["off", "2,8kW"]}},
        "sensor.speicher_oben": {"state": "45", "attributes": {"unit_of_measurement": "°C"}},
    }
    snapshot = Snapshot(entries=[], devices=[], entities=[], states=states, energy_prefs=None)
    rod = DeviceConfig(
        id="ww", ha_device_id="manual:ww", profile="generic/stepped", profile_version=1, name="Warmwasser",
        kind="heater", power_steps={"off": 0, "2,8kW": 2800}, parameters={"max_power": 2800, "tank_l": 300},
        role_overrides={"level": RoleOverride(entity_id="select.heizstab"), "temperature": RoleOverride(entity_id="sensor.speicher_oben")},
    )
    start = datetime(2026, 10, 30, 12, tzinfo=UTC)

    loads, infos, _, _ = flex_loads(SunflowConfig(devices=[rod]), library.profiles, snapshot,
                                     [start + timedelta(minutes=15 * i) for i in range(96)], ZoneInfo("Europe/Berlin"))

    (info,) = infos
    assert (info.kind, info.current, info.target) == ("heater", 45.0, 60.0)
    assert loads[0].energy_wh == pytest.approx(15 * 300 * WATER_WH_PER_LITRE_KELVIN)


@pytest.mark.parametrize(
    ("states", "expected"),
    [
        pytest.param(
            {"sensor.soc": {"state": "62", "last_updated": "2026-10-30T10:00:00+00:00"}, "binary_sensor.plug": {"state": "on"}},
            {"soc_pct": 62.0, "energy_wh": 23250.0, "soc_unavailable": False, "plugged": True},
            id="readable",
        ),
        pytest.param(
            {"sensor.soc": {"state": "unavailable"}, "binary_sensor.plug": {"state": "not_home"}},
            {"soc_pct": None, "energy_wh": None, "soc_unavailable": True, "plugged": False},
            id="unavailable-away",
        ),
    ],
)
def test_vehicle_live_values(states: dict, expected: dict) -> None:
    """SoC, energy in the battery and plugged state from the car's own sensors."""
    from sunflow.api.vehicles import vehicle_live
    from sunflow.config.models import Vehicle

    vehicle = Vehicle(name="Fiat", capacity_wh=37500, soc_entity="sensor.soc", plugged_entity="binary_sensor.plug")
    live = vehicle_live(vehicle, states)
    assert {k: live[k] for k in expected} == expected
