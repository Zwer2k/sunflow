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

    # A removed forecast entry is not created again
    await hass.config_entries.async_remove(plane.entry_id)
    app = next(e for e in entries if e.title == "Sunflow")
    await app.runtime_data.forecasts.async_refresh()
    await hass.async_block_till_done()
    assert len(hass.config_entries.async_entries("sunflow")) == 2
