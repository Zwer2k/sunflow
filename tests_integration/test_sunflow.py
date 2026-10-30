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

    result = await hass.config_entries.flow.async_init("sunflow", context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"url": URL, "token": "t"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entries = hass.config_entries.async_entries("sunflow")
    assert sorted(e.title for e in entries) == ["PV-Prognose · Gesamt", "PV-Prognose · Nord-Ost (Fläche)", "Sunflow"]
    assert all(e.state is ConfigEntryState.LOADED for e in entries)

    from custom_components.sunflow.energy import async_get_solar_forecast

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
    assert len(hass.config_entries.async_entries("sunflow")) == 2
