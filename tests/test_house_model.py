"""Learning how the house cools and warms from its room temperatures."""

from datetime import UTC, datetime, timedelta
import math
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest
from sunflow.api.house import router
from sunflow.config.models import HouseSettings, Room
from sunflow.config.store import ConfigStore
from sunflow.planning.house_model import (
    HourRow,
    carnot_cop,
    chart,
    fit,
    restless_rooms,
    simulate,
    weighted_inside,
)

NOW = datetime(2026, 11, 16, tzinfo=UTC)
HOUR = timedelta(hours=1)
# A house with τ = 50 h, 25 kWh/K, sun +0.3 K/h, internal gains +0.02 K/h
A, B, C, D = 0.02, 0.04, 0.3, 0.02


def _house(days: int, heat: bool = True, sun: bool = True) -> list[HourRow]:
    """Hourly rows of a simulated house: outside swings daily, the heat pump runs in the morning and evening."""
    rows = []
    inside = 21.0
    start = NOW - timedelta(days=days)
    for i in range(days * 24):
        moment = start + i * HOUR
        hour = moment.hour
        outside = 3 + 5 * math.sin((hour - 9) / 24 * 2 * math.pi) + (i % 7) * 0.3
        heat_kwh = 6.0 if hour in (5, 6, 7, 17, 18, 19) else 1.0 if hour % 3 == 0 else 0.0
        sunshine = max(math.sin((hour - 6) / 12 * math.pi), 0.0) * (0.3 + (i // 24 % 3) * 0.35)
        rows.append(HourRow(start=moment, inside=inside, outside=outside, heat=heat_kwh if heat else None, sun=sunshine if sun else None))
        inside += A * (outside - inside) + B * heat_kwh + C * sunshine + D
    return rows


def test_learns_a_known_house() -> None:
    """The coefficients of a simulated house come back, and it beats "it stays as it is"."""
    model = fit(_house(21), NOW)

    assert model is not None
    assert model.tau_h == pytest.approx(50, rel=0.05)
    assert model.capacity_kwh_per_k == pytest.approx(25, rel=0.05)
    assert model.loss_w_per_k == pytest.approx(500, rel=0.05)
    assert model.c == pytest.approx(C, rel=0.1)
    assert model.useful


def test_without_heat_or_sun() -> None:
    """Without a heat measurement or sun the model still fits, without those terms."""
    model = fit(_house(21, heat=False, sun=False), NOW)

    assert model is not None
    assert (model.b, model.c, model.capacity_kwh_per_k) == (None, None, None)


def test_too_few_days() -> None:
    """Fewer than seven days are not enough."""
    assert fit(_house(5), NOW) is None


def test_simulation_starts_again_after_a_gap() -> None:
    """Every 24 hours and after a gap the simulation starts from the measurement."""
    rows = _house(8)
    rows = rows[:30] + rows[31:]
    model = fit(_house(21), NOW)
    assert model is not None

    predicted = simulate(model, rows)

    assert predicted[0] is None
    assert predicted[24] is None
    assert predicted[30] is None
    assert predicted[1] == pytest.approx(rows[1].inside, abs=0.05)


def test_chart_has_the_last_week() -> None:
    """The chart shows measured and expected temperatures of the last seven days."""
    rows = _house(21)
    points = chart(fit(rows, NOW), rows)

    assert len(points) == 7 * 24
    assert points[0]["model"] is None
    assert points[1]["model"] == pytest.approx(points[1]["measured"], abs=0.1)


def test_weighted_rooms_and_restless_ones() -> None:
    """Rooms count by weight; a room that jumps far more than the others is pointed out."""
    hours = [NOW - i * HOUR for i in range(72)]
    calm = {f"sensor.r{i}": {h: 20.0 + 0.01 * k for k, h in enumerate(hours)} for i in range(3)}
    calm["sensor.sun"] = {h: 20.0 + (3.0 if k % 2 else 0.0) for k, h in enumerate(hours)}
    weights = dict.fromkeys(calm, 1.0) | {"sensor.r0": 2.0}

    inside = weighted_inside(calm, weights)

    assert inside[hours[0]] == pytest.approx(20.0)
    assert restless_rooms(calm, inside) == ["sensor.sun"]


@pytest.mark.parametrize(("outside", "cop"), [(-10.0, 2.74), (7.0, 4.40), (40.0, 6.0)])
def test_carnot_cop(outside: float, cop: float) -> None:
    """A rough COP from the Carnot limit when nothing measures it."""
    assert carnot_cop(outside) == pytest.approx(cop, abs=0.01)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param({"comfort_min": 22, "comfort_max": 21}, id="band upside down"),
        pytest.param(
            {"rooms": [{"entity_id": "sensor.a", "reference": True}, {"entity_id": "sensor.b", "reference": True}]}, id="two references"
        ),
        pytest.param({"night_start": "25:00"}, id="no time"),
    ],
)
def test_house_settings_refused(body: dict[str, object]) -> None:
    """A band upside down, two reference rooms or a wrong time are refused."""
    with pytest.raises(ValidationError):
        HouseSettings.model_validate(body)


def test_house_settings_api(tmp_path: Path) -> None:
    """Rooms and band are stored; without rooms there is nothing to learn."""
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.state.store = ConfigStore(tmp_path / "config.json")
    client = TestClient(app)

    assert client.get("/api/v1/house/model").json() == {"state": "no_rooms"}
    body = HouseSettings(rooms=[Room(entity_id="sensor.wohnzimmer", reference=True)], night_min=18).model_dump()
    assert client.put("/api/v1/config/house", json=body).json() == body
    assert client.get("/api/v1/config/house").json() == body
