"""Tests of the Sunflow integration; run inside a Home Assistant core checkout (see README)."""

from typing import Any

import pytest

from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant.test_util.aiohttp import AiohttpClientMocker

URL = "http://sunflow:8099"
FORECAST = {
    "generated": "x",
    "targets": [
        {"id": "total", "kind": "total", "name": "Gesamt", "plant": None, "wh_hours": {"2026-10-30T11:00:00+00:00": 1500},
         "today_wh": 12000, "remaining_today_wh": 4000, "tomorrow_wh": 9000, "power_now_w": 1500},
        {"id": "plane:solcast:ne", "kind": "plane", "name": "Nord-Ost", "plant": "Sofar",
         "wh_hours": {"2026-10-30T11:00:00+00:00": 500}, "today_wh": 4000, "remaining_today_wh": 1000, "tomorrow_wh": 3000,
         "power_now_w": 500},
    ],
}
STATE = {
    "time": "x",
    "control": {"enabled": False, "paused": None},
    "strategy": {"default": "balanced", "effective": "balanced"},
    "plan": {"updated": None, "error": None, "action": "hold", "action_power_w": None, "price_ct": 31.2, "feed_in_ct": 8.0,
             "cheapest_start": "2026-10-31T01:00:00+00:00", "cheapest_price_ct": 22.6, "soc_in_an_hour_pct": 55.0,
             "savings_eur": 1.23, "grid_charging": False, "curtailing": False},
    "live": {"pv_w": 5000, "load_w": 800, "grid_w": -3000, "battery_w": 1200, "soc_pct": 50.0, "surplus_w": 4200, "surplus": True},
    "calendar": [{"summary": "Wallbox lädt", "start": "2026-10-30T13:00:00+00:00", "end": "2099-10-30T15:00:00+00:00",
                  "description": "8,0 kWh geplant"}],
    "loads": [],
    "fallback": [{"domain": "input_boolean", "service": "turn_off", "data": {}, "target": {"entity_id": "input_boolean.charge"}}],
    "devices": [{"id": "wb", "name": "Wallbox", "enabled": True, "controlled": False, "action": "charge", "power_w": 4000,
                 "reason": "nach Plan", "phases": 3, "limit_w": None, "error": None}],
    "consumers": [{"id": "wb", "name": "Wallbox", "kind": "vehicle", "level": 40.0, "target": 80.0, "unit": "%",
                   "vehicle": "Fiat", "need_wh": 12000, "planned_wh": 8000, "next_start": "2026-10-30T13:00:00+00:00",
                   "running": False, "reason": None}],
}
STRINGS = {"strings": [{"id": "sofar:1", "device_id": "sofar", "plant": "Sofar", "name": "Sofar String 1", "plane": None,
                        "energy_wh": 12345.6, "power_w": 800}]}


@pytest.fixture
def custom_integrations_enabled() -> bool:
    """Load custom_components/sunflow."""
    return True


async def test_setup_forecast_entries_and_energy(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Manual setup, one entry per forecast, energy forecast, sensors."""
    aioclient_mock.get(f"{URL}/api/v1/health", json={"status": "ok"})
    aioclient_mock.get(f"{URL}/api/v1/strings/energy", json=STRINGS)
    aioclient_mock.get(f"{URL}/api/v1/forecast/export", json=FORECAST)
    aioclient_mock.get(f"{URL}/api/v1/integration/state", json=STATE)
    aioclient_mock.put(f"{URL}/api/v1/control/takeover?confirm=true", json={})
    aioclient_mock.put(f"{URL}/api/v1/control/strategy", json={})
    aioclient_mock.post(f"{URL}/api/v1/integration/replan", json={})

    result = await hass.config_entries.flow.async_init("sunflow_ems", context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": "t"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries("sunflow_ems")
    assert sorted(e.title for e in entries) == ["PV-Prognose · Gesamt", "PV-Prognose · Nord-Ost (Fläche)", "Sunflow"]
    assert all(e.state is ConfigEntryState.LOADED for e in entries)

    from custom_components.sunflow_ems.energy import async_get_solar_forecast

    plane = next(e for e in entries if e.title.startswith("PV-Prognose · Nord"))
    assert await async_get_solar_forecast(hass, plane.entry_id) == {"wh_hours": {"2026-10-30T11:00:00+00:00": 500}}

    states = {s.entity_id: s.state for s in hass.states.async_all("sensor")}
    assert "12.346" in states.values()
    assert "4.0" in states.values()

    assert hass.states.get("sensor.sunflow_battery_action").state == "hold"
    assert hass.states.get("sensor.sunflow_wallbox_action").state == "off"
    assert hass.states.get("switch.sunflow_control_wallbox").state == "on"
    assert hass.states.get("binary_sensor.sunflow_pv_surplus").state == "on"
    assert hass.states.get("binary_sensor.sunflow_plan").state == "on"
    assert hass.states.get("sensor.sunflow_wallbox_next_start").state == "2026-10-30T13:00:00+00:00"

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.sunflow_sunflow_controls"}, blocking=True)
    await hass.services.async_call(
        "select", "select_option", {"entity_id": "select.sunflow_strategy", "option": "autarky"}, blocking=True
    )
    await hass.services.async_call("button", "press", {"entity_id": "button.sunflow_plan_again"}, blocking=True)
    calls = [(method, str(url), body) for method, url, body, _ in aioclient_mock.mock_calls if method != "GET"]
    assert calls == [
        ("PUT", f"{URL}/api/v1/control/takeover?confirm=true", {"enabled": True}),
        ("PUT", f"{URL}/api/v1/control/strategy", {"strategy": "autarky", "until": None}),
        ("POST", f"{URL}/api/v1/integration/replan", None),
    ]

    # A removed forecast entry is not created again
    await hass.config_entries.async_remove(plane.entry_id)
    app = next(e for e in entries if e.title == "Sunflow")
    await app.runtime_data.forecasts.async_refresh()
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries("sunflow_ems")) == 2


async def _setup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, state: dict[str, Any]) -> None:
    aioclient_mock.get(f"{URL}/api/v1/health", json={"status": "ok"})
    aioclient_mock.get(f"{URL}/api/v1/strings/energy", json=STRINGS)
    aioclient_mock.get(f"{URL}/api/v1/forecast/export", json=FORECAST)
    aioclient_mock.get(f"{URL}/api/v1/integration/state", json=state)
    result = await hass.config_entries.flow.async_init("sunflow_ems", context={"source": SOURCE_USER})
    await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": "t"})
    await hass.async_block_till_done()


async def test_plan_load_events_and_calendar(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """plan_load answers with the start; a load that starts fires sunflow_ems_load_start; the calendar shows the plan."""
    from pytest_homeassistant.common import async_capture_events

    load = {"id": "l1", "name": "Waschmaschine", "energy_wh": 1500, "power_w": 2000, "finish_by": None,
            "start": "2026-10-30T13:00:00+00:00", "end": "2026-10-30T14:00:00+00:00", "started": False, "cost_eur": 0.12}
    aioclient_mock.post(f"{URL}/api/v1/integration/loads", json=load)
    await _setup(hass, aioclient_mock, STATE)

    response = await hass.services.async_call(
        "sunflow_ems", "plan_load", {"name": "Waschmaschine", "energy_kwh": 1.5, "power_kw": 2}, blocking=True,
        return_response=True,
    )
    assert response == {"id": "l1", "start": load["start"], "end": load["end"], "cost_eur": 0.12}
    (method, url, body, _) = next(c for c in aioclient_mock.mock_calls if c[0] == "POST")
    assert body == {"name": "Waschmaschine", "energy_wh": 1500.0, "power_w": 2000.0, "earliest": None, "finish_by": None}

    events = async_capture_events(hass, "sunflow_ems_load_start")
    app = next(e for e in hass.config_entries.async_entries("sunflow_ems") if e.title == "Sunflow")
    app.runtime_data.state.async_set_updated_data({**STATE, "loads": [{**load, "started": True}]})
    await hass.async_block_till_done()
    assert [e.data["name"] for e in events] == ["Waschmaschine"]

    assert hass.states.get("calendar.sunflow_plan").attributes["message"] == "Wallbox lädt"


async def test_watchdog_hands_back_when_the_app_is_gone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: Any
) -> None:
    """Ten minutes without answer: the App's hand-back calls run once and a repair issue appears."""
    from datetime import timedelta

    from pytest_homeassistant.common import async_fire_time_changed, async_mock_service

    from homeassistant.helpers import issue_registry as ir

    calls = async_mock_service(hass, "input_boolean", "turn_off")
    await _setup(hass, aioclient_mock, STATE)
    aioclient_mock.clear_requests()
    aioclient_mock.get(f"{URL}/api/v1/integration/state", status=500)
    aioclient_mock.get(f"{URL}/api/v1/strings/energy", status=500)
    aioclient_mock.get(f"{URL}/api/v1/forecast/export", status=500)

    for _ in range(12):
        freezer.tick(timedelta(minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert [c.data["entity_id"] for c in calls] == ["input_boolean.charge"]
    assert ir.async_get(hass).async_get_issue("sunflow_ems", "app_unreachable") is not None


async def test_forecasts_follow_the_app(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A forecast the App no longer offers loses its entry; offered again, it comes back."""
    await _setup(hass, aioclient_mock, STATE)
    app = next(e for e in hass.config_entries.async_entries("sunflow_ems") if e.title == "Sunflow")
    coordinator = app.runtime_data.forecasts

    coordinator.async_set_updated_data({**FORECAST, "targets": FORECAST["targets"][:1]})
    await hass.async_block_till_done()
    assert sorted(e.title for e in hass.config_entries.async_entries("sunflow_ems")) == ["PV-Prognose · Gesamt", "Sunflow"]

    coordinator.async_set_updated_data(FORECAST)
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries("sunflow_ems")) == 3


async def test_plug_in_suggestion_fires_once(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A car at home that gets energy soon fires sunflow_ems_plug_in_suggested once, not on every update."""
    from pytest_homeassistant.common import async_capture_events

    hint = {"vehicle_id": "fiat", "vehicle": "Fiat 500e", "wallbox_id": "wb", "wallbox": "Wallbox",
            "start": "2026-10-30T13:00:00+00:00", "energy_wh": 8200, "pv_wh": 6100}
    await _setup(hass, aioclient_mock, STATE)
    events = async_capture_events(hass, "sunflow_ems_plug_in_suggested")
    app = next(e for e in hass.config_entries.async_entries("sunflow_ems") if e.title == "Sunflow")

    for _ in range(2):
        app.runtime_data.state.async_set_updated_data({**STATE, "plug_in": [hint]})
        await hass.async_block_till_done()

    assert [e.data for e in events] == [
        {"vehicle": "Fiat 500e", "wallbox": "Wallbox", "start": hint["start"], "energy_kwh": 8.2, "pv_kwh": 6.1}
    ]


HOUSE = {
    "setpoint_c": 21.0, "mode": "heat", "min_c": 18.0, "max_c": 27.0, "step_c": 0.5, "room_c": 20.6,
    "rooms_used": ["sensor.wohnzimmer"], "rooms_missing": [], "rooms_stale": [], "plan_target_c": 21.0,
    "shadow": True,
    "heat_pumps": [{"id": "hp", "name": "HPSU", "activity": "heating", "compressor": True,
                    "flow": {"shadow": True, "strategy": "fixed", "curve_c": 31.0, "outside_c": 2.0, "correction_k": 0.4, "target_c": 31.4,
                             "frozen": [], "reason": None}}],
}


async def test_climate_house(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """The climate entity mirrors the App; quick changes are sent once with the last value; out of range is refused."""
    from datetime import timedelta

    from homeassistant.exceptions import ServiceValidationError
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant.common import async_fire_time_changed

    aioclient_mock.put(f"{URL}/api/v1/house/climate", json={"setpoint_c": 21.3, "mode": "off"})
    await _setup(hass, aioclient_mock, {**STATE, "house": HOUSE})

    state = hass.states.get("climate.sunflow_house")
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 20.6
    assert state.attributes["temperature"] == 21.0
    assert (state.attributes["min_temp"], state.attributes["max_temp"], state.attributes["target_temp_step"]) == (18.0, 27.0, 0.5)
    assert state.attributes["hvac_action"] == "heating"
    assert (state.attributes["flow_target_temperature"], state.attributes["flow_correction"], state.attributes["shadow"]) == (31.4, 0.4, True)

    for value in (22.0, 21.5, 21.3):
        await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.sunflow_house", "temperature": value}, blocking=True)
    await hass.services.async_call("climate", "set_hvac_mode", {"entity_id": "climate.sunflow_house", "hvac_mode": "off"}, blocking=True)
    assert hass.states.get("climate.sunflow_house").attributes["temperature"] == 21.3
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=5))
    await hass.async_block_till_done()

    puts = [body for method, url, body, _ in aioclient_mock.mock_calls if method == "PUT"]
    assert puts == [{"setpoint_c": 21.3, "mode": "off"}]
    state = hass.states.get("climate.sunflow_house")
    assert (state.state, state.attributes["temperature"]) == ("off", 21.3)

    for value in (17.5, 27.5):
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call("climate", "set_temperature", {"entity_id": "climate.sunflow_house", "temperature": value}, blocking=True)


async def test_no_climate_without_house(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await _setup(hass, aioclient_mock, {**STATE, "house": None})

    assert hass.states.get("climate.sunflow_house") is None


SETPOINT = {
    "mode": "shadow", "written_c": 32.0, "written_at": "2026-11-22T10:00:00+00:00", "written_reason": "normal",
    "pending_c": 31.0, "pending_at": "2026-11-22T10:05:00+00:00", "pending_reason": "normal", "held": "spacing",
    "held_budget": None, "writes_today": 3,
}


def _house(setpoint: dict[str, Any] | None, pumps: int = 1) -> dict[str, Any]:
    flow = {**HOUSE["heat_pumps"][0]["flow"], "setpoint": setpoint}
    names = [("hp", "HPSU"), ("hp2", "Werkstatt")][:pumps]
    return {**HOUSE, "heat_pumps": [{**HOUSE["heat_pumps"][0], "id": i, "name": n, "flow": flow} for i, n in names]}


async def test_heat_pump_sensors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Flow target, set value and writes today per heat pump; in the shadow marked by mode and the ghost icon."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass, aioclient_mock, {**STATE, "house": _house(SETPOINT)})

    target = hass.states.get("sensor.sunflow_hpsu_flow_target")
    setpoint = hass.states.get("sensor.sunflow_hpsu_setpoint")
    writes = hass.states.get("sensor.sunflow_hpsu_writes_today")
    assert (target.state, target.attributes["unit_of_measurement"], target.attributes["device_class"]) == ("31.4", "°C", "temperature")
    assert target.attributes["state_class"] == "measurement"
    assert setpoint.state == "32.0" and setpoint.attributes["state_class"] == "measurement"
    assert {k: setpoint.attributes[k] for k in ("mode", "pending", "pending_at", "pending_reason", "written_at", "written_reason", "held")} == {
        "mode": "shadow", "pending": 31.0, "pending_at": "2026-11-22T10:05:00+00:00", "pending_reason": "normal",
        "written_at": "2026-11-22T10:00:00+00:00", "written_reason": "normal", "held": "spacing",
    }
    assert "shadow" not in setpoint.attributes
    # A count going back to 0 each day: no unit, no state class (history only)
    assert writes.state == "3" and "state_class" not in writes.attributes and "unit_of_measurement" not in writes.attributes
    assert all(s.attributes["icon"] == "mdi:ghost-outline" and s.attributes["mode"] == "shadow" for s in (target, setpoint, writes))
    # The unique id from Sunflow's id of the heat pump, not its name
    registry = er.async_get(hass)
    assert registry.async_get("sensor.sunflow_hpsu_setpoint").unique_id.endswith("_heat_pump_hp_setpoint")

    climate = hass.states.get("climate.sunflow_house").attributes
    assert {k: climate[k] for k in (
        "mode", "shadow", "setpoint_written", "setpoint_written_at", "setpoint_pending", "setpoint_pending_at",
        "setpoint_pending_reason", "setpoint_held",
    )} == {
        "mode": "shadow", "shadow": True, "setpoint_written": 32.0, "setpoint_written_at": "2026-11-22T10:00:00+00:00",
        "setpoint_pending": 31.0, "setpoint_pending_at": "2026-11-22T10:05:00+00:00", "setpoint_pending_reason": "normal",
        "setpoint_held": "spacing",
    }

    # Active: mode and icon change, name and entity id stay; nothing known yet: unknown, never 0
    app = hass.config_entries.async_entries("sunflow_ems")[0]
    app.runtime_data.state.async_set_updated_data({**STATE, "house": _house({**SETPOINT, "mode": "active", "written_c": None, "writes_today": None})})
    await hass.async_block_till_done()
    setpoint = hass.states.get("sensor.sunflow_hpsu_setpoint")
    writes = hass.states.get("sensor.sunflow_hpsu_writes_today")
    assert (setpoint.state, setpoint.attributes["mode"], setpoint.attributes["icon"]) == ("unknown", "active", "mdi:thermometer-water")
    assert (writes.state, writes.attributes["icon"]) == ("unknown", "mdi:counter")
    assert hass.states.get("sensor.sunflow_hpsu_flow_target").attributes["icon"] == "mdi:thermometer-water"
    climate = hass.states.get("climate.sunflow_house").attributes
    assert (climate["mode"], climate["shadow"]) == ("active", False)


async def test_heat_pump_sensors_follow_the_heat_pumps(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A heat pump added later gets its sensors; one removed in Sunflow takes them along."""
    from homeassistant.helpers import entity_registry as er

    await _setup(hass, aioclient_mock, {**STATE, "house": _house(SETPOINT)})
    app = hass.config_entries.async_entries("sunflow_ems")[0]
    coordinator = app.runtime_data.state
    registry = er.async_get(hass)

    coordinator.async_set_updated_data({**STATE, "house": _house(SETPOINT, pumps=2)})
    await hass.async_block_till_done()
    assert hass.states.get("sensor.sunflow_werkstatt_setpoint").state == "32.0"
    climate = hass.states.get("climate.sunflow_house").attributes
    assert {k: climate["heat_pumps"]["Werkstatt"][k] for k in ("written", "pending", "held", "mode")} == {
        "written": 32.0, "pending": 31.0, "held": "spacing", "mode": "shadow",
    }

    # Without the house in the state (older App): nothing removed
    coordinator.async_set_updated_data({k: v for k, v in STATE.items() if k != "house"})
    await hass.async_block_till_done()
    assert registry.async_get("sensor.sunflow_werkstatt_setpoint") is not None

    coordinator.async_set_updated_data({**STATE, "house": _house(SETPOINT, pumps=1)})
    await hass.async_block_till_done()
    assert registry.async_get("sensor.sunflow_werkstatt_setpoint") is None
    assert hass.states.get("sensor.sunflow_werkstatt_setpoint") is None
    assert hass.states.get("sensor.sunflow_hpsu_setpoint").state == "32.0"

    coordinator.async_set_updated_data({**STATE, "house": None})
    await hass.async_block_till_done()
    assert registry.async_get("sensor.sunflow_hpsu_flow_target") is None
