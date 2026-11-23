"""History of a heat pump for the view: flow target and room setpoint per 5 minute row (schema 11) and the
endpoint /heat_pump/<id>/history with room temperature per row and the events to mark."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api.heat_pump import history_rows, router
from sunflow.config.store import ConfigStore
from sunflow.heat_pump.shadow import FlowShadow
from sunflow.heat_pump.state_store import HeatPumpStateStore

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)


def test_room_mean_leaves_out_rooms_with_a_fast_change() -> None:
    """The room temperature of a row is weighted like the live one; a room with a fast change does not count."""
    rows = [
        {"start": "a", "rooms": {"sensor.a": 20.0, "sensor.b": 23.0, "sensor.x": 30.0}, "flags": {"valid": True, "ventilation": []}},
        {"start": "b", "rooms": {"sensor.a": 20.0, "sensor.b": 17.0}, "flags": {"ventilation": ["sensor.b"], "blocked": True}},
        {"start": "c", "rooms": {}, "flags": None},
    ]

    first, second, third = history_rows(rows, {"sensor.a": 1.0, "sensor.b": 2.0})

    assert first["room_c"] == pytest.approx(22.0)
    # Rooms that are not configured are left out
    assert first["rooms"] == {"sensor.a": 20.0, "sensor.b": 23.0}
    assert (first["valid"], first["blocked"]) == (True, False)
    assert second["room_c"] == pytest.approx(20.0)
    assert (second["ventilation"], second["blocked"], second["valid"]) == (["sensor.b"], True, False)
    assert third["room_c"] is None


async def test_rows_keep_flow_target_and_room_setpoint(tmp_path: Path, library: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """Each stored row notes the flow target and the room setpoint; the endpoint returns them with the events."""
    from test_heat_pump_samples import Pump  # noqa: PLC0415

    class Clock(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return (NOW + 12 * MIN).astimezone(tz)

    monkeypatch.setattr("sunflow.api.heat_pump.datetime", Clock)
    pump = Pump(tmp_path, library)
    pump.recorder.flow = FlowShadow(HeatPumpStateStore(tmp_path))
    try:
        await pump.minutes(NOW, 3)
        # Pause: the start rule would raise Max T-Vorlauf (a write event)
        pump.states["status_kompressor"] = ("off", None)
        pump.states["mode_of_operating"] = ("Bereitschaft", None)
        pump.states["tv"] = ("28.0", "°C")
        await pump.minutes(NOW + 3 * MIN, 8)
        rows = pump.rows()
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.state.profiles = library
        app.state.store = ConfigStore(tmp_path / "config", lambda: library.profiles)
        config = app.state.store.load()
        config.devices = pump.config.devices
        config.house = pump.config.house
        app.state.store.save(config)
        app.state.runner = SimpleNamespace(heat_pump=pump.recorder, shadow=pump.store)
        client = TestClient(app)
        answer = client.get("/api/v1/heat_pump/hp/history", params={"hours": 1}).json()
        missing = client.get("/api/v1/heat_pump/nope/history")
        too_long = client.get("/api/v1/heat_pump/hp/history", params={"hours": 24 * 8})
    finally:
        pump.store.close()

    assert rows and all(r["room_setpoint_c"] == pytest.approx(pump.config.house.climate.setpoint_c) for r in rows)
    assert any(r["target_c"] is not None for r in rows)
    assert len(answer["rows"]) == len(rows)
    last = answer["rows"][-1]
    assert last["room_c"] == pytest.approx(21.0)
    assert last["target_c"] == rows[-1]["target_c"]
    assert last["room_setpoint_c"] == pytest.approx(pump.config.house.climate.setpoint_c)
    assert [e["kind"] for e in answer["events"] if e["kind"] == "write"] == ["write"]
    assert answer["rooms"] == [{"entity_id": "sensor.wohnzimmer", "weight": 1.0}]
    assert missing.status_code == 404
    assert too_long.status_code == 422
