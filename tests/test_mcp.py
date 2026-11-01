"""Tests for the read-only MCP server."""

from pathlib import Path
import sqlite3
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.mcp_key import McpKey
from sunflow.settings import Settings


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    """The App with its MCP token, without Home Assistant."""
    settings = Settings(
        ha_url="http://localhost:8123",
        ha_token="x",
        port=0,
        data_dir=tmp_path,
        profiles_dir=tmp_path,
        schema_dir=tmp_path,
        frontend_dir=tmp_path / "missing",
        ingress_only=True,
    )
    app = create_app(settings)
    app.state.settings = settings
    app.state.mcp_key = McpKey(tmp_path)
    app.state.token = app.state.mcp_key.create()
    app.state.integration_token = "integration-secret"
    store = ConfigStore(tmp_path)
    store.save(SunflowConfig(devices=[DeviceConfig(id="d", ha_device_id="x", profile="p", profile_version=1, name="Sofar")]))
    app.state.store = store
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """A client outside the Supervisor network, like one on a mapped host port, with the key."""
    client = TestClient(app, client=("192.168.1.20", 50000))
    client.headers["Authorization"] = f"Bearer {app.state.token}"
    return client


def _rpc(client: TestClient, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200
    return response.json()


def test_token_required_and_only_for_mcp(client: TestClient) -> None:
    """Without its token /mcp is closed; the token opens nothing else."""
    message = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    assert client.post("/mcp", json=message, headers={"Authorization": ""}).status_code == 401
    assert client.post("/mcp", json=message, headers={"Authorization": "Bearer integration-secret"}).status_code == 401
    assert client.post("/mcp", json=message).json() == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert client.get("/api/v1/config").status_code == 403
    assert client.put("/api/v1/control/takeover", json={}).status_code == 403


def test_initialize_and_list(client: TestClient) -> None:
    """The handshake answers the client's protocol version; every tool is marked read only."""
    result = _rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"] == {"tools": {}}
    notified = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert notified.status_code == 202

    tools = _rpc(client, "tools/list")["result"]["tools"]
    assert {"get_plan", "get_history", "get_control_log", "query_shadow_db"} <= {t["name"] for t in tools}
    assert all(t["annotations"]["readOnlyHint"] for t in tools)


def test_tool_reads_api(client: TestClient) -> None:
    """A tool answers with the JSON of its GET endpoint."""
    result = _rpc(client, "tools/call", {"name": "get_config", "arguments": {}})["result"]
    assert '"name":"Sofar"' in result["content"][0]["text"].replace(" ", "")
    assert "isError" not in result


def test_tool_errors(client: TestClient) -> None:
    """Unknown tools, missing arguments and failing endpoints come back as tool errors."""
    unknown = _rpc(client, "tools/call", {"name": "set_anything", "arguments": {}})["result"]
    assert unknown["isError"]
    missing = _rpc(client, "tools/call", {"name": "get_device_parameters", "arguments": {}})["result"]
    assert missing == {"content": [{"type": "text", "text": "device_id fehlt"}], "isError": True}
    assert _rpc(client, "nope")["error"]["code"] == -32601


def test_sql_is_read_only(client: TestClient, tmp_path: Path) -> None:
    """The shadow database can be queried but not changed."""
    db = sqlite3.connect(tmp_path / "shadow.db")
    db.execute("CREATE TABLE slots (start TEXT PRIMARY KEY, actual_pv_w REAL)")
    db.execute("INSERT INTO slots VALUES ('2026-11-01T10:00:00+00:00', 3200.0)")
    db.commit()
    db.close()

    def sql(statement: str) -> dict[str, Any]:
        return _rpc(client, "tools/call", {"name": "query_shadow_db", "arguments": {"sql": statement}})["result"]

    rows = sql("SELECT start, actual_pv_w FROM slots")
    assert rows["content"][0]["text"] == (
        '{"columns":["start","actual_pv_w"],"rows":[["2026-11-01T10:00:00+00:00",3200.0]],"truncated":false}'
    )
    assert sql("DELETE FROM slots")["isError"]
    assert sql(f"ATTACH DATABASE '{tmp_path / 'other.db'}' AS other")["isError"]
    assert not (tmp_path / "other.db").exists()
    assert sql("PRAGMA query_only = OFF")["isError"]
    assert sql("SELECT name FROM sqlite_master WHERE type = 'table'")["content"][0]["text"] == '{"columns":["name"],"rows":[["slots"]],"truncated":false}'
    assert sqlite3.connect(tmp_path / "shadow.db").execute("SELECT count(*) FROM slots").fetchone() == (1,)


def test_key_shown_once_and_revocable(app: FastAPI, client: TestClient, tmp_path: Path) -> None:
    """A key is only returned when created, kept as a hash, and stops working when replaced or revoked."""
    ingress = TestClient(app, client=("172.30.32.2", 50000))
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    link = ingress.get("/api/v1/mcp/link").json()
    assert link["port"] == 0
    assert "key" not in link
    assert link["key_created"]
    assert app.state.token not in (tmp_path / "mcp_key.json").read_text()
    assert client.get("/api/v1/mcp/link").status_code == 403
    assert client.post("/api/v1/mcp/key").status_code == 403

    new = ingress.post("/api/v1/mcp/key").json()["key"]
    assert client.post("/mcp", json=ping).status_code == 401
    assert client.post("/mcp", json=ping, headers={"Authorization": f"Bearer {new}"}).status_code == 200
    # Survives a restart
    app.state.mcp_key = McpKey(tmp_path)
    assert client.post("/mcp", json=ping, headers={"Authorization": f"Bearer {new}"}).status_code == 200

    assert ingress.delete("/api/v1/mcp/key").json() == {"key_created": None}
    assert client.post("/mcp", json=ping, headers={"Authorization": f"Bearer {new}"}).status_code == 401
    assert McpKey(tmp_path).created is None


def test_legacy_plain_key_removed(tmp_path: Path) -> None:
    """The key 0.68.0 stored in plain text is deleted; a new one has to be created."""
    (tmp_path / "mcp_token").write_text("old")
    assert McpKey(tmp_path).created is None
    assert not (tmp_path / "mcp_token").exists()
