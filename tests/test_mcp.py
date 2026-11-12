"""Tests for the read-only MCP server."""

import json
import logging
from pathlib import Path
import sqlite3
from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app, mcp
from sunflow.config.models import DeviceConfig, SunflowConfig
from sunflow.config.store import ConfigStore
from sunflow.log_buffer import LogBuffer
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
    app.state.ha = AsyncMock()
    app.state.ha.command.return_value = {"internal": "http://192.168.1.10:8123", "external": "https://ha.example.org"}
    link = ingress.get("/api/v1/mcp/link").json()
    app.state.ha.command.assert_awaited_once_with("network/url")
    assert link["host"] == "192.168.1.10"
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


def test_areas_limit_the_tools(app: FastAPI, client: TestClient) -> None:
    """Areas switched off in the settings disappear from the list and refuse calls; status stays."""
    ingress = TestClient(app, client=("172.30.32.2", 50000))
    assert ingress.put("/api/v1/config/mcp", json={"areas": ["live"]}).json() == {"areas": ["live"]}

    names = {t["name"] for t in _rpc(client, "tools/list")["result"]["tools"]}
    assert {"get_status", "get_plan"} <= names
    assert not names & {"get_config", "query_shadow_db", "get_log", "get_control_log"}
    refused = _rpc(client, "tools/call", {"name": "get_config", "arguments": {}})["result"]
    assert refused["isError"]
    assert "nicht freigegeben" in refused["content"][0]["text"]
    assert ingress.put("/api/v1/config/mcp", json={"areas": ["secrets"]}).status_code == 422
    assert client.put("/api/v1/config/mcp", json={"areas": ["log"]}).status_code == 403


def test_log_tool(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """The log tool returns recent records from a level up, with the traceback of exceptions."""
    buffer = LogBuffer()
    monkeypatch.setattr(mcp, "LOG_BUFFER", buffer)
    logger = logging.getLogger("sunflow.test_mcp")
    logger.addHandler(buffer)
    logger.setLevel(logging.INFO)
    try:
        logger.info("Replanned")
        logger.warning("Replan failed: %s", "Not connected")
        try:
            raise ValueError("boom")
        except ValueError:
            logger.exception("Control failed")
    finally:
        logger.removeHandler(buffer)

    def log(arguments: dict[str, Any]) -> list[dict[str, Any]]:
        result = _rpc(client, "tools/call", {"name": "get_log", "arguments": arguments})["result"]
        return json.loads(result["content"][0]["text"])["entries"]

    assert [e["message"] for e in log({})] == ["Replan failed: Not connected", "Control failed"]
    assert [e["message"] for e in log({"level": "info", "limit": 1})] == ["Control failed"]
    error = log({"level": "error"})
    assert len(error) == 1
    assert "ValueError: boom" in error[0]["traceback"]
    assert _rpc(client, "tools/call", {"name": "get_log", "arguments": {"level": "debug"}})["result"]["isError"]


@pytest.mark.parametrize(
    "sql",
    [
        pytest.param("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c", id="endless recursion"),
        pytest.param("SELECT zeroblob(50000000)", id="huge value"),
    ],
)
def test_query_is_stopped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sql: str) -> None:
    """A query must not keep a thread for good or build a value that fills the memory."""
    monkeypatch.setattr(mcp, "SQL_TIMEOUT_S", 0.2)
    path = tmp_path / "shadow.db"
    sqlite3.connect(path).close()

    with pytest.raises(mcp.ToolError):
        mcp.query_shadow(path, sql)
