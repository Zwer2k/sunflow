"""Tests for recognizing the vehicle at a wallbox."""

from types import SimpleNamespace

import pytest

from sunflow.config_store import DeviceConfig, SunflowConfig, Vehicle, VehicleDetection
from sunflow.vehicles import recognize, scanned_card

FIAT = Vehicle(id="fiat", name="Fiat 500e", capacity_wh=37500, card=1, plugged_entity="binary_sensor.fiat_plugged", wallboxes=["wb"])
TESLA = Vehicle(id="tesla", name="Model 3", capacity_wh=60000, card=2, plugged_entity="binary_sensor.tesla_plugged", wallboxes=["wb"])


def _config(vehicles: list[Vehicle], detection: VehicleDetection | None = None, chosen: str | None = None) -> SunflowConfig:
    wallbox = DeviceConfig(id="wb", ha_device_id="w", profile="goecharger_mqtt/charger", profile_version=1, name="Wallbox")
    if detection:
        wallbox.vehicle_detection = detection
    return SunflowConfig(devices=[wallbox], vehicles=vehicles, selected_vehicles={"wb": chosen} if chosen else {})


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
