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
