"""Tests for the Home Assistant websocket client against a fake server."""

from collections.abc import AsyncIterator
from typing import Any

from aiohttp import WSMsgType, web
from aiohttp.test_utils import TestServer
import pytest

from sunflow.ha import HAClient, HAError

TOKEN = "secret"


async def _fake_ha(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required"})
    auth = await ws.receive_json()
    if auth.get("access_token") != TOKEN:
        await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
        await ws.close()
        return ws
    await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})

    async for raw in ws:
        if raw.type is not WSMsgType.TEXT:
            break
        msg: dict[str, Any] = raw.json()
        msg_id = msg["id"]
        match msg["type"]:
            case "get_states":
                await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": [{"entity_id": "sun.sun", "state": "above_horizon"}]})
            case "energy/get_prefs":
                await ws.send_json({"id": msg_id, "type": "result", "success": False, "error": {"code": "not_found", "message": "No prefs"}})
            case "subscribe_events":
                await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": None})
                await ws.send_json({"id": msg_id, "type": "event", "event": {"event_type": "state_changed"}})
            case "stall":
                # Core stalled: never answers
                pass
            case "malformed":
                await ws.send_json({"id": msg_id, "type": "result"})
            case "call_service":
                await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": {"echo": msg}})
            case _:
                await ws.send_json({"id": msg_id, "type": "result", "success": False, "error": {"code": "unknown_command", "message": "Unknown command."}})
    return ws


@pytest.fixture
async def server() -> AsyncIterator[TestServer]:
    """Run the fake Home Assistant websocket server."""
    app = web.Application()
    app.router.add_get("/api/websocket", _fake_ha)
    async with TestServer(app) as srv:
        yield srv


@pytest.fixture
async def client(server: TestServer) -> AsyncIterator[HAClient]:
    """Return a connected client."""
    ha = HAClient(str(server.make_url("/api/websocket")), TOKEN)
    await ha.connect()
    yield ha
    await ha.close()


async def test_connect_and_command(client: HAClient) -> None:
    """Authentication succeeds and results are returned."""
    assert client.connected
    assert client.ha_version == "2026.11.0"
    assert await client.get_states() == [{"entity_id": "sun.sun", "state": "above_horizon"}]


async def test_invalid_token(server: TestServer) -> None:
    """A wrong token raises HAError."""
    ha = HAClient(str(server.make_url("/api/websocket")), "wrong")
    with pytest.raises(HAError, match="Invalid access token"):
        await ha.connect()
    assert not ha.connected


async def test_error_result(client: HAClient) -> None:
    """A failed command raises HAError with the error code."""
    with pytest.raises(HAError, match="unknown_command"):
        await client.command("does/not/exist")


async def test_energy_prefs_not_configured(client: HAClient) -> None:
    """Missing energy preferences return None."""
    assert await client.get_energy_prefs() is None


async def test_subscribe(client: HAClient) -> None:
    """Events are delivered to the subscription callback."""
    events: list[dict[str, Any]] = []
    await client.subscribe(events.append, event_type="state_changed")
    await client.get_states()  # round trip ensures the event was processed
    assert events == [{"event_type": "state_changed"}]


async def test_call_service(client: HAClient) -> None:
    """Actions are sent with data and target."""
    result = await client.call_service(
        "number", "set_value", {"value": 500}, {"entity_id": "number.x"}
    )
    assert result["echo"]["service_data"] == {"value": 500}
    assert result["echo"]["target"] == {"entity_id": "number.x"}


async def test_large_message(server: TestServer) -> None:
    """Results above aiohttp's 4 MB default do not drop the connection."""
    ha = HAClient(str(server.make_url("/api/websocket")), TOKEN)
    await ha.connect()
    try:
        result = await ha.call_service("number", "set_value", {"blob": "x" * (5 * 1024 * 1024)})
        assert len(result["echo"]["service_data"]["blob"]) == 5 * 1024 * 1024
        assert ha.connected
    finally:
        await ha.close()


async def test_unanswered_command_times_out(client: HAClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """A command without an answer fails instead of blocking every later request."""
    from sunflow.ha import client as client_module

    monkeypatch.setattr(client_module, "COMMAND_TIMEOUT", 0.2)
    with pytest.raises(HAError, match="No answer"):
        await client.command("stall")
    assert client.connected


async def test_malformed_result_keeps_reader(client: HAClient) -> None:
    """A result without "success" fails that command; the connection keeps working."""
    with pytest.raises(HAError):
        await client.command("malformed")
    assert (await client.get_states())[0]["entity_id"] == "sun.sun"
