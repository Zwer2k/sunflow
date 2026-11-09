"""A read-only MCP server (Model Context Protocol), so an AI assistant can analyse what Sunflow sees and plans.

Streamable HTTP without sessions: every JSON-RPC request is one POST to /mcp, answered as
JSON. The tools only read: they call the GET endpoints of the API inside the process, the
SQL tool opens the shadow database read only, and the log tool reads the App's recent log.
Nothing can be configured or controlled. Which areas are readable is set in the settings.
"""

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from sunflow import __version__
from sunflow.log_buffer import LOG_BUFFER

router = APIRouter()

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26")
SQL_MAX_ROWS = 2000
SQL_TIMEOUT_S = 10
LOG_MAX_ENTRIES = 500
LOG_LEVELS = {"error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO}

HOURS = {"type": "integer", "minimum": 1, "description": "Stunden zurück"}
DAYS = {"type": "integer", "minimum": 1, "description": "Tage zurück"}
DEVICE = {"type": "string", "description": "Geräte-ID aus get_config"}


@dataclass(frozen=True, kw_only=True)
class Tool:
    """An MCP tool answered by one GET endpoint; {name} in the path is taken from the arguments."""

    name: str
    description: str
    path: str
    # Readable when this area is allowed in the settings; None: always
    area: str | None
    params: dict[str, Any] = field(default_factory=dict)
    required: tuple[str, ...] = ()

    def schema(self) -> dict[str, Any]:
        """The tool as tools/list describes it."""
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": self.params, "required": list(self.required)},
            "annotations": {"readOnlyHint": True, "openWorldHint": False},
        }


TOOLS = [
    Tool(name="get_status", area=None, description="Version und Verbindung zu Home Assistant.", path="/health"),
    Tool(name="get_live", area="live", description="Aktuelle Leistungen: PV, Speicher, Netz, Haus, Verbraucher.", path="/live"),
    Tool(
        name="get_plan", area="live",
        description="Aktueller Plan (15-Minuten-Slots, bis 96 h): Aktion, Speicher, Preise, Prognosen, Verbraucher, Warnungen.",
        path="/plan/latest",
    ),
    Tool(name="get_control", area="control", description="Steuerung: Strategien, Übersteuerungen, Prioritäten, nächste Fenster.", path="/control"),
    Tool(
        name="get_control_log", area="control",
        description="Was Sunflow an Geräte geschrieben hat, Fehler dabei und Änderungen durch andere (bis 14 Tage).",
        path="/control/log",
        params={"hours": HOURS, "device_id": DEVICE},
    ),
    Tool(
        name="get_history", area="history",
        description="Gemessene Werte (SoC, PV, Last, Netz, Verbraucher, Ladestände) aus HA-Statistiken, bis 7 Tage am Stück.",
        path="/history",
        params={"hours": HOURS, "end": {"type": "string", "description": "Ende (ISO 8601), Standard: jetzt"}},
    ),
    Tool(
        name="get_shadow", area="history",
        description="Aufgezeichnete Slots: geplant gegen tatsächlich, mit Auswertung.",
        path="/shadow",
        params={"hours": HOURS},
    ),
    Tool(
        name="get_energy", area="history",
        description="Energiebilanz eines Zeitraums.",
        path="/energy",
        params={"period": {"type": "string", "enum": ["today", "7d", "30d"]}},
    ),
    Tool(name="get_costs", area="history", description="Kosten und Ersparnis je Tag.", path="/costs/history", params={"days": DAYS}),
    Tool(name="get_efficiency", area="history", description="Wirkungsgrad-Verlauf je Tag.", path="/efficiency/history", params={"days": DAYS}),
    Tool(
        name="get_data_inventory", area="history",
        description="Wie weit die HA-Historie zurückreicht: Zustände, Langzeitstatistik, Wetterattribute, vergangene Solcast-Prognosen.",
        path="/data-inventory",
        params={"entities": {"type": "string", "description": "Weitere Entitäten, kommagetrennt (z. B. Wetterstation)"}},
    ),
    Tool(
        name="get_learning_sources", area="config",
        description="Gewählte Lernquellen (Außenklima, Helligkeit, Niederschlag, Modultemperatur) mit Kandidaten.",
        path="/config/learning-sources",
    ),
    Tool(
        name="get_learning_source_quality", area="history",
        description="Plausibilität der Lernquellen über bis zu 100 Tage: Anteil brauchbar, Ausfälle, Ausreißer, hängende Werte.",
        path="/config/learning-sources/quality",
    ),
    Tool(
        name="get_appliances", area="history",
        description="Verbraucher, die nur gemessen werden (Herd, Waschmaschine …): Verbrauch je Tag, aktive Tage, übliche Stunden.",
        path="/appliances/usage",
    ),
    Tool(name="get_recommendations", area="live", description="Empfehlungen von Sunflow.", path="/recommendations"),
    Tool(name="get_config", area="config", description="Konfiguration: Geräte, Tarif, Prognose, Fahrzeuge, Steuerung.", path="/config"),
    Tool(name="get_sources", area="live", description="Preis- und Prognosequellen mit ihrem Zustand.", path="/sources"),
    Tool(
        name="get_tariff_series", area="live",
        description="Strompreise der nächsten Stunden (auch geschätzte).",
        path="/tariff/series",
        params={"hours": HOURS | {"description": "Stunden voraus"}},
    ),
    Tool(
        name="get_forecast_series", area="live",
        description="PV-Prognose der nächsten Stunden, roh und gelernt korrigiert.",
        path="/forecast/series",
        params={"hours": HOURS | {"description": "Stunden voraus"}},
    ),
    Tool(name="get_pv_learning", area="live", description="Stand des PV-Lernens je Fläche, mit Verschmutzung (state: Sprünge, verschmutzte Tage, Niveau).", path="/pv-learning"),
    Tool(
        name="get_pv_sun", area="history",
        description="PV-Korrektur nach Sonnenstand je Fläche; plant mit, wenn eingeschaltet und die Fläche plannable ist: Faktoren je Sonnenhöhe/-richtung und Backtest gegen Rohprognose und Stundenmodell. Rechnet im Hintergrund; computing=true heißt später erneut fragen.",
        path="/pv-learning/sun",
    ),
    Tool(name="get_vehicles", area="config", description="Fahrzeuge und Ladeeinstellungen.", path="/config/vehicles"),
    Tool(name="get_batteries", area="config", description="Speicher mit Kapazität und Modulen.", path="/config/batteries"),
    Tool(name="get_site", area="config", description="Standort und Anlage.", path="/site"),
    Tool(name="get_integration_state", area="live", description="Was die HA-Integration bekommt (Sensoren, Kalender).", path="/integration/state"),
    Tool(name="get_direct_status", area="config", description="Zustand direkt angebundener Geräte (Modbus TCP, HTTP).", path="/direct/status"),
    Tool(
        name="get_device_parameters", area="config",
        description="Parameter eines Geräts mit aktuellen Werten.",
        path="/config/devices/{device_id}/parameters",
        params={"device_id": DEVICE},
        required=("device_id",),
    ),
    Tool(
        name="get_device_efficiency", area="config",
        description="Gemessener Wirkungsgrad eines Speichers.",
        path="/config/devices/{device_id}/efficiency",
        params={"device_id": DEVICE, "days": DAYS},
        required=("device_id",),
    ),
]

SQL_TOOL = {
    "name": "query_shadow_db",
    "description": (
        "Nur lesende SQL-Abfrage (SQLite) auf die Aufzeichnung shadow.db, z. B. die Tabelle slots "
        "(start, planned_*, actual_*, actual_consumers, actual_levels als JSON). "
        f"Höchstens {SQL_MAX_ROWS} Zeilen; alle Tabellen stehen in sqlite_master."
    ),
    "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
LOG_TOOL = {
    "name": "get_log",
    "description": (
        "Fehler, Warnungen und Hinweise aus dem Log der App (seit dem letzten Start, im Speicher), "
        "mit Traceback bei Ausnahmen; älteste zuerst."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "level": {"type": "string", "enum": ["error", "warning", "info"], "description": "Mindeststufe, Standard warning"},
            "hours": HOURS,
            "limit": {"type": "integer", "minimum": 1, "maximum": LOG_MAX_ENTRIES, "description": "Höchstens so viele, die neuesten"},
        },
        "required": [],
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": False},
}
LOCAL_TOOLS = {SQL_TOOL["name"]: (SQL_TOOL, "history"), LOG_TOOL["name"]: (LOG_TOOL, "log")}
TOOLS_BY_NAME = {tool.name: tool for tool in TOOLS}


class ToolError(Exception):
    """A tool call that failed; reported to the client as a tool result with isError."""


async def call_get(app: Any, path: str, query: dict[str, Any]) -> tuple[int, bytes]:
    """GET an API path inside the process, through the same middleware as from outside."""
    body = bytearray()
    status = 500
    sent = False

    async def receive() -> dict[str, Any]:
        nonlocal sent
        if sent:
            # Only read once the response is complete
            await asyncio.Event().wait()
        sent = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, Any]) -> None:
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": urlencode({k: v for k, v in query.items() if v is not None}).encode(),
        "headers": [(b"host", b"localhost")],
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0),
    }
    await app(scope, receive, send)
    return status, bytes(body)


READ_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}


def _read_only(action: int, *_: Any) -> int:
    return sqlite3.SQLITE_OK if action in READ_ACTIONS else sqlite3.SQLITE_DENY


def query_shadow(path: Path, sql: str) -> dict[str, Any]:
    """Run one statement on the shadow database, opened read only."""
    if not path.exists():
        raise ToolError("Noch keine Aufzeichnung vorhanden")
    db = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, timeout=SQL_TIMEOUT_S)
    try:
        db.execute("PRAGMA query_only = ON")
        # query_only still allows ATTACH (which creates files) and setting pragmas
        db.set_authorizer(_read_only)
        cursor = db.execute(sql)
        columns = [c[0] for c in cursor.description or ()]
        rows = cursor.fetchmany(SQL_MAX_ROWS + 1)
    except sqlite3.Error as err:
        raise ToolError(f"SQL-Fehler: {err}") from err
    finally:
        db.close()
    return {"columns": columns, "rows": rows[:SQL_MAX_ROWS], "truncated": len(rows) > SQL_MAX_ROWS}


def allowed_areas(request: Request) -> set[str]:
    """Areas the MCP server may read, as set in the settings."""
    return set(request.app.state.store.load().mcp.areas)


def tool_list(areas: set[str]) -> list[dict[str, Any]]:
    """The tools of the allowed areas."""
    return [
        *(t.schema() for t in TOOLS if t.area is None or t.area in areas),
        *(schema for schema, area in LOCAL_TOOLS.values() if area in areas),
    ]


def read_log(arguments: dict[str, Any]) -> str:
    """Recent log records from a level up."""
    level = LOG_LEVELS.get(str(arguments.get("level") or "warning"))
    if level is None:
        raise ToolError("level muss error, warning oder info sein")
    try:
        hours = max(int(arguments.get("hours") or 24), 1)
        limit = min(max(int(arguments.get("limit") or 100), 1), LOG_MAX_ENTRIES)
    except TypeError, ValueError:
        raise ToolError("hours und limit müssen Zahlen sein") from None
    entries = LOG_BUFFER.entries(level, datetime.now(UTC) - timedelta(hours=hours), limit)
    return json.dumps({"entries": entries}, separators=(",", ":"))


async def call_tool(request: Request, name: str, arguments: dict[str, Any]) -> str:
    """The result of a tool as text (JSON)."""
    area = LOCAL_TOOLS[name][1] if name in LOCAL_TOOLS else getattr(TOOLS_BY_NAME.get(name), "area", "")
    if name not in LOCAL_TOOLS and name not in TOOLS_BY_NAME:
        raise ToolError(f"Unbekanntes Werkzeug: {name}")
    if area is not None and area not in allowed_areas(request):
        raise ToolError(f"{name} ist in Sunflow nicht freigegeben (Einstellungen → Geräte → MCP-Server)")
    if name == SQL_TOOL["name"]:
        data_dir: Path = request.app.state.settings.data_dir
        result = await asyncio.to_thread(query_shadow, data_dir / "shadow.db", str(arguments.get("sql", "")))
        return json.dumps(result, default=str, separators=(",", ":"))
    if name == LOG_TOOL["name"]:
        return read_log(arguments)
    tool = TOOLS_BY_NAME[name]
    query = {k: v for k, v in arguments.items() if k in tool.params}
    path = tool.path
    for key in tool.required:
        if not isinstance(value := query.pop(key, None), str) or not value:
            raise ToolError(f"{key} fehlt")
        path = path.replace(f"{{{key}}}", quote(value, safe=""))
    status, body = await call_get(request.app, f"/api/v1{path}", query)
    text = body.decode(errors="replace")
    if status >= 400:
        raise ToolError(f"HTTP {status}: {text}")
    return text


def _result(message_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


async def handle(request: Request, message: Any) -> dict[str, Any] | None:
    """Answer one JSON-RPC message; None for notifications."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
    if "id" not in message:
        return None
    message_id, method = message["id"], message["method"]
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        return _result(
            message_id,
            {
                "protocolVersion": requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sunflow", "version": __version__},
                "instructions": (
                    "Sunflow ist ein Energiemanagement für Home Assistant (PV, Speicher, Wallbox, Warmwasser, "
                    "dynamische Tarife). Alle Werkzeuge lesen nur. Zeiten sind UTC (ISO 8601), Leistungen in W, "
                    "Energie in Wh."
                ),
            },
        )
    if method == "ping":
        return _result(message_id, {})
    if method == "tools/list":
        return _result(message_id, {"tools": tool_list(allowed_areas(request))})
    if method == "tools/call":
        arguments = params.get("arguments") or {}
        try:
            text = await call_tool(request, str(params.get("name")), arguments if isinstance(arguments, dict) else {})
        except ToolError as err:
            return _result(message_id, {"content": [{"type": "text", "text": str(err)}], "isError": True})
        return _result(message_id, {"content": [{"type": "text", "text": text}]})
    return _error(message_id, -32601, f"Method not found: {method}")


@router.post("/mcp", include_in_schema=False)
async def mcp(request: Request) -> Response:
    """One JSON-RPC message (or a batch) of an MCP client."""
    try:
        payload = await request.json()
    except ValueError:
        return JSONResponse(_error(None, -32700, "Parse error"), status_code=400)
    if isinstance(payload, list):
        answers = [a for m in payload if (a := await handle(request, m)) is not None]
        return JSONResponse(answers) if answers else Response(status_code=202)
    answer = await handle(request, payload)
    return JSONResponse(answer) if answer is not None else Response(status_code=202)


@router.get("/mcp", include_in_schema=False)
async def mcp_stream() -> Response:
    """No server-initiated messages: there is nothing to stream."""
    return Response(status_code=405, headers={"Allow": "POST"})
