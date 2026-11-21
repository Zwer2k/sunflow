"""The house: the rooms Sunflow learns it from and what it learned."""

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from sunflow.config.models import HouseSettings
from sunflow.config.store import ConfigStore
from sunflow.ha import HAError
from sunflow.planning.house_model import (
    MIN_DAYS,
    SAMPLES,
    chart,
    heat_pump_device,
    learn_house,
    restless_rooms,
)

from .common import _library, _replan

router = APIRouter()
# Learning reads four weeks of hourly statistics: kept for a while
CACHE_FOR = timedelta(minutes=30)
# Room temperature at which the cooling rate is shown
COOLING_INSIDE_C = 20.0


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
    request.app.state.house_model = None
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.relearn_house()
    _replan(request)
    return config.house.model_dump()


@router.get("/house/model")
async def house_model(request: Request) -> dict[str, Any]:
    """How the house cools and warms, learned from the rooms; with the last week measured and expected."""
    config = request.app.state.store.load()
    if not config.house.rooms:
        return {"state": "no_rooms"}
    now = datetime.now(UTC)
    # Everything the model learns from: rooms, sources and the heat pump's sensors
    profiles = _library(request).profiles
    pump = heat_pump_device(config, profiles)
    key = "|".join((
        config.house.model_dump_json(),
        config.learning_sources.model_dump_json(),
        pump.model_dump_json(include={"role_overrides"}) if pump else "",
    ))
    cached = getattr(request.app.state, "house_model", None)
    runner = getattr(request.app.state, "runner", None)
    # Whether the runner's model (learned a few times a day) predicts better than "it stays"
    used = {"used": getattr(runner, "house_model", None) is not None}
    if cached and cached[0] == key and now - cached[1] < CACHE_FOR:
        return cached[2] | used
    flow = getattr(getattr(runner, "heat_pump", None), "flow", None)
    try:
        snapshots = getattr(request.app.state, "snapshots", None)
        snapshot = await snapshots.get() if snapshots is not None else None
        # The same choice of source as the runner (heat_pump_samples once chosen stays)
        learned = await learn_house(
            request.app.state.ha, config, profiles, now, snapshot, getattr(runner, "shadow", None),
            kept=getattr(flow, "house_source", None) == SAMPLES,
        )
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    data, model = learned.data, learned.model
    if data.outside_entity is None:
        result: dict[str, Any] = {"state": "no_outside"}
    else:
        result = {
            "state": "learning" if model is None else "ready" if model.useful else "unsure",
            "days": model.days if model else len({r.start.date() for r in data.rows}),
            "min_days": MIN_DAYS,
            # Where the hourly rows come from (9.3): ha_hourly until heat_pump_samples covers MIN_DAYS
            # days of valid heating, then heat_pump_samples
            "source": data.source,
            "coverage_days": data.coverage_days,
            "heat_source": data.heat_source,
            "outside_entity": data.outside_entity,
            # Hours with hot water or defrost (from the samples also after-run, ventilation), left out
            "excluded_hours": data.excluded_hours,
            "sun": data.sun_entity is not None,
            "restless": restless_rooms(data.per_room, data.inside),
            "model": None if model is None else {
                "tau_h": model.tau_h,
                "capacity_kwh_per_k": model.capacity_kwh_per_k,
                "loss_w_per_k": model.loss_w_per_k,
                "sun_k_per_h": model.c,
                "gains_k_per_h": model.d,
                "cooling_k_per_h_at_0": model.cooling_k_per_h(COOLING_INSIDE_C, 0.0),
                "error_k": model.error_k,
                "baseline_k": model.baseline_k,
                "plausible": model.plausible,
            },
            "chart": chart(model, data.rows),
        }
    request.app.state.house_model = (key, now, result)
    return result | used
