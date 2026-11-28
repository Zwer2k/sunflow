"""Pairing EEBUS wallboxes through the Sunflow EEBUS App."""

from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.config.store import ConfigStore
from sunflow.devices.profiles import load_profiles, load_schema
from sunflow.direct.hub import DirectHub

REPO = Path(__file__).resolve().parents[1]
SKI = "0123456789abcdef0123456789abcdef01234567"
TOKEN = "sunflows-key"


@pytest.fixture
def eebus_app() -> Iterator[ThreadingHTTPServer]:
    """The EEBUS App's API in a thread of its own: its SKI, one wallbox found, trusting it; it knows TOKEN."""
    trusted: list[tuple[str, object]] = []

    class Handler(BaseHTTPRequestHandler):
        def _answer(self, body: object) -> None:
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _allowed(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {TOKEN}":
                return True
            self.send_response(401)
            self.end_headers()
            return False

        def do_GET(self) -> None:  # noqa: N802 - the name http.server calls
            if self._allowed():
                self._answer({"ski": "own", "version": "test", "services": [{"ski": SKI, "brand": "Elli"}]})

        def do_POST(self) -> None:  # noqa: N802
            if self._allowed():
                length = int(self.headers.get("Content-Length") or 0)
                trusted.append((self.path.removeprefix("/trust/"), json.loads(self.rfile.read(length)) if length else None))
                self._answer({"result": "ok"})

        def log_message(self, *_: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.trusted = trusted  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()


def _app(tmp_path: Path, token: str = TOKEN) -> FastAPI:
    app = create_app()
    app.state.eebus_token = token
    app.state.store = ConfigStore(tmp_path)
    app.state.profiles = load_profiles(REPO / "profiles", load_schema(REPO / "schema"))
    app.state.direct = DirectHub()
    app.state.runner = SimpleNamespace(request_replan=lambda: None)
    return app


def test_pair_and_add_an_eebus_wallbox(tmp_path: Path, eebus_app: ThreadingHTTPServer) -> None:
    """The wallbox is trusted with its SKI as shown (groups, upper case), in another segment with its IP, and added."""
    client = TestClient(_app(tmp_path))
    where = {"host": "127.0.0.1", "port": eebus_app.server_address[1]}
    shown = "0123 4567 89AB CDEF 0123 4567 89AB CDEF 0123 4567"

    found = client.get("/api/v1/eebus/status", params=where).json()
    trusted = client.post(f"/api/v1/eebus/trust/{shown}", params=where, json={"ip": "192.168.20.15", "port": 4711}).json()
    device = client.post("/api/v1/config/devices/direct", json={"profile": "eebus/wallbox", "remote": shown} | where).json()

    assert ([s["ski"] for s in found["services"]], found["token_ok"]) == ([SKI], True)
    assert trusted == {"ski": SKI}
    assert eebus_app.trusted == [(SKI, {"ip": "192.168.20.15", "port": 4711})]  # type: ignore[attr-defined]
    assert device["connection"]["remote"] == SKI


def test_the_eebus_app_without_sunflows_token(tmp_path: Path, eebus_app: ThreadingHTTPServer) -> None:
    """The App does not know Sunflow's token yet: Sunflow shows it to be entered there (no 401 to the UI)."""
    client = TestClient(_app(tmp_path, token="another"))
    where = {"host": "127.0.0.1", "port": eebus_app.server_address[1]}

    status = client.get("/api/v1/eebus/status", params=where)

    assert (status.status_code, status.json()) == (200, {"token_ok": False, "token": "another"})
    assert client.post(f"/api/v1/eebus/trust/{SKI}", params=where).status_code == 502


@pytest.mark.parametrize(
    ("method", "request_path", "code"),
    [
        pytest.param("POST", "/api/v1/eebus/trust/123", 422, id="not-an-ski"),
        # The App is not installed or not running
        pytest.param("GET", "/api/v1/eebus/status?host=127.0.0.1&port=1", 502, id="app-unreachable"),
    ],
)
def test_eebus_errors(tmp_path: Path, method: str, request_path: str, code: int) -> None:
    """A wrong SKI is refused; an App that does not answer is a gateway error with a reason."""
    assert TestClient(_app(tmp_path)).request(method, request_path).status_code == code
