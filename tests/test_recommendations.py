"""Tests for the recommendations derived from settings, plan and measured data."""

from sunflow.config_store import DeviceConfig, FlexSettings, OptimizerConfig, SunflowConfig
from sunflow.efficiency import EfficiencyEstimate
from sunflow.recommendations import build


def _estimate(round_trip: float, conversion: float | None = None, standby: float | None = None) -> EfficiencyEstimate:
    return EfficiencyEstimate(
        efficiency=round_trip**0.5, round_trip=round_trip, hours=2000, charged_kwh=990, discharged_kwh=720,
        stored_change_kwh=0, cycles=49.7, reliable=True, plausible=True, source="counters",
        conversion_round_trip=conversion, standby_w=standby,
    )


def _config(**changes: object) -> SunflowConfig:
    devices = [
        DeviceConfig(id="bat", ha_device_id="b", profile="solax_modbus/sofar", profile_version=1, name="Sofar",
                     parameters={"efficiency_pct": 73.1}),
        DeviceConfig(id="ww", ha_device_id="w", profile="evcc_intg/loadpoint", profile_version=1, name="Warmwasser",
                     parameters={"tank_l": 300, "daily_energy_wh": 9000}, flex=FlexSettings(alternative_cost_ct=22.8)),
    ]
    return SunflowConfig(devices=devices, **changes)


def test_efficiency_suggests_conversion_value() -> None:
    """The split conversion efficiency is offered as one-click action."""
    found = build(_config(), None, {"bat": _estimate(0.731, 0.937, 89)}, [], 0, None, [])

    rec = next(r for r in found if r["id"] == "battery_efficiency:bat")
    assert rec["severity"] == "warn"
    assert rec["action"] == {"type": "set_parameter", "device_id": "bat", "name": "efficiency_pct", "value": 93.7}
    assert "89 W" in rec["text"]


def test_hot_water_demand_plausibility() -> None:
    """9 kWh per day for 300 l means about 26 K: worth a check."""
    found = build(_config(), None, {}, [], 0, None, [])

    assert any(r["id"] == "hot_water_demand:ww" for r in found)


def test_dismissed_stays_hidden_until_numbers_change() -> None:
    """Dismissing hides exactly this state of the recommendation."""
    first = next(r for r in build(_config(), None, {"bat": _estimate(0.731, 0.937)}, [], 0, None, []) if r["id"] == "battery_efficiency:bat")
    config = _config(dismissed_recommendations=[f"{first['id']}#{first['fingerprint']}"])

    assert not any(r["id"] == "battery_efficiency:bat" for r in build(config, None, {"bat": _estimate(0.731, 0.937)}, [], 0, None, []))
    # Clearly different numbers bring it back
    assert any(r["id"] == "battery_efficiency:bat" for r in build(config, None, {"bat": _estimate(0.731, 0.88)}, [], 0, None, []))


def test_weather_without_cloud_coverage() -> None:
    """Only weather entities without cloud coverage: suggest one that has it."""
    found = build(_config(), None, {}, [], 0, None, [{"entity_id": "weather.x", "cloud_coverage": False}])

    assert any(r["id"] == "weather_without_clouds" for r in found)


def test_consumer_usage_recommendations() -> None:
    """Measured use far from the settings is offered to take over."""
    from types import SimpleNamespace

    from sunflow.habits import ConsumerUsage

    config = SunflowConfig(devices=[
        DeviceConfig(id="bike", ha_device_id="manual:1", profile="generic/switch", profile_version=1, name="E-Bike",
                     parameters={"max_power": 500}),
    ])
    latest = SimpleNamespace(usages={"bike": ConsumerUsage(daily_wh=520, running_w=250, days=5)}, flex=[], plan=SimpleNamespace(flex=[], slots=[]))

    found = {r["id"]: r for r in build(config, latest, {}, [], 0, None, [])}

    assert found["consumer_daily:bike"]["action"]["value"] == 520
    assert found["consumer_power:bike"]["action"] == {"type": "set_parameter", "device_id": "bike", "name": "max_power", "value": 250}
