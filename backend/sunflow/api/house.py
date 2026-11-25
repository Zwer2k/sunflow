"""The house: the rooms Sunflow learns it from."""

from typing import Any

from fastapi import APIRouter, Request

from sunflow.config.models import HouseSettings
from sunflow.config.store import ConfigStore

from .common import _replan

router = APIRouter()


@router.get("/config/house")
async def get_house(request: Request) -> dict[str, Any]:
    """The rooms and the heat meter."""
    return request.app.state.store.load().house.model_dump()


@router.put("/config/house")
async def set_house(request: Request, body: HouseSettings) -> dict[str, Any]:
    """Choose the rooms and the heat meter; the room setpoint is kept (changed by PUT /house/climate)."""
    store: ConfigStore = request.app.state.store
    config = store.load()
    # A setpoint changed in Home Assistant while the page was open must not be overwritten
    body.climate = config.house.climate
    config.house = body
    store.save(config)
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.relearn_house()
    _replan(request)
    return config.house.model_dump()
