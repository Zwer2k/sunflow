"""Tests for serving the frontend under a versioned asset path."""

from pathlib import Path

from fastapi.testclient import TestClient

from sunflow.api import create_app
from sunflow.settings import Settings


def _client(frontend: Path) -> TestClient:
    settings = Settings(
        ha_url="http://localhost:8123",
        ha_token="x",
        port=0,
        data_dir=frontend / "data",
        profiles_dir=frontend,
        schema_dir=frontend,
        frontend_dir=frontend,
    )
    # Not used as context manager: lifespan (HA connection) is not started
    return TestClient(create_app(settings))


def test_versioned_assets(tmp_path: Path) -> None:
    """Index references assets by version; the version changes with the files."""
    (tmp_path / "src").mkdir()
    (tmp_path / "index.html").write_text('<script src="./__ASSETS__/src/app.js"></script>')
    (tmp_path / "src" / "app.js").write_text("console.log(1)")
    client = _client(tmp_path)

    index = client.get("/")
    assert index.headers["cache-control"] == "no-cache"
    path = index.text.split('"./')[1].split('"')[0]
    assert path.startswith("assets/") and path.endswith("/src/app.js")

    asset = client.get(f"/{path}")
    assert asset.text == "console.log(1)"
    assert "immutable" in asset.headers["cache-control"]

    (tmp_path / "src" / "app.js").write_text("console.log(2)")
    assert client.get("/").text != index.text


def test_asset_outside_frontend_is_rejected(tmp_path: Path) -> None:
    """Paths escaping the frontend directory are not served."""
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("")
    (tmp_path / "secret.txt").write_text("secret")
    client = _client(frontend)

    assert client.get("/assets/x/%2E%2E/secret.txt").status_code == 404
    assert client.get("/assets/x/missing.js").status_code == 404
