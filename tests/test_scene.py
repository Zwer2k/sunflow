"""What goes where in the live view's house pictures."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sunflow.api import create_app
from sunflow.config.store import ConfigStore

SCENE = {"roofs": {"inv:0": "carport", "inv:1": "hidden"}, "inverters": ["inv"], "consumers": {"wp": "heatpump", "tv": "house"}}


def _client(tmp_path: Path) -> TestClient:
    app = create_app()
    app.state.store = ConfigStore(tmp_path)
    return TestClient(app)


def test_scene_is_stored_apart_from_the_display(tmp_path: Path) -> None:
    """The display page saves without the scene and does not undo it."""
    client = _client(tmp_path)

    assert client.put("/api/v1/config/display/scene", json=SCENE).json() == SCENE
    client.put("/api/v1/config/display", json={"weather_mobile": False})

    assert client.get("/api/v1/config/display").json()["scene"] == SCENE


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"roofs": {"inv": "garden"}}, id="unknown roof face"),
        pytest.param({"consumers": {"wp": "pool"}}, id="unknown figure"),
        pytest.param({"inverters": ["a", "b", "c"]}, id="three inverters"),
    ],
)
def test_scene_refuses_unknown_places(tmp_path: Path, body: dict[str, object]) -> None:
    """Unknown roof faces or figures and more than two inverter places are refused."""
    assert _client(tmp_path).put("/api/v1/config/display/scene", json=body).status_code == 422
