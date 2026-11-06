"""Helpers shared by the API routers."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
import logging
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from sunflow import API_VERSION
from sunflow.config.models import DeviceConfig
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import EepromGuard
from sunflow.devices.profiles import ProfileLibrary
from sunflow.ha import HAError

_LOGGER = logging.getLogger(__name__)


DEFAULT_MAX_EEPROM_WRITES = 10
# An older result is still answered at once (while it is refreshed) up to this age
STALE_MAX = timedelta(minutes=15)


router = APIRouter(prefix=f"/api/{API_VERSION}")


def _fresh_cache(state: Any) -> tuple[dict[Any, tuple[datetime, Any]], dict[Any, asyncio.Task[Any]]]:
    if getattr(state, "kept_fresh", None) is None:
        state.kept_fresh = {}
        state.kept_fresh_tasks = {}
    return state.kept_fresh, state.kept_fresh_tasks


def keep_fresh(state: Any, key: Any, value: Any) -> Any:
    """Store a result computed in the background for kept_fresh."""
    _fresh_cache(state)[0][key] = (datetime.now(UTC), value)
    return value


def _log_failed_refresh(task: asyncio.Task[Any]) -> None:
    # A failed refresh keeps the older result; the next request tries again
    if not task.cancelled() and (err := task.exception()) is not None:
        _LOGGER.debug("Refresh in the background failed: %s", err)


async def kept_fresh(request: Request, key: Any, max_age: timedelta, compute: Callable[[], Awaitable[Any]]) -> Any:
    """A result computed at most max_age ago; an older one is answered at once and refreshed behind.

    Opening a page should not wait for Home Assistant's history every time; the next poll
    gets the refreshed result.
    """
    state = request.app.state
    cache, tasks = _fresh_cache(state)
    now = datetime.now(UTC)

    async def refresh() -> Any:
        return keep_fresh(state, key, await compute())

    if (entry := cache.get(key)) is not None and now - entry[0] <= max_age:
        return entry[1]
    if entry is not None and now - entry[0] <= STALE_MAX:
        if key not in tasks or tasks[key].done():
            tasks[key] = asyncio.create_task(refresh())
            tasks[key].add_done_callback(_log_failed_refresh)
        return entry[1]
    return await refresh()


async def _snapshot(request: Request, fresh: bool = False) -> Snapshot:
    try:
        return await request.app.state.snapshots.get(fresh=fresh)
    except HAError as err:
        raise HTTPException(status_code=502, detail=str(err)) from err


def _library(request: Request) -> ProfileLibrary:
    return request.app.state.profiles


def _configured_device(request: Request, device_id: str) -> DeviceConfig:
    config = request.app.state.store.load()
    if (device := config.device(device_id)) is None:
        raise HTTPException(status_code=404, detail="Device not configured")
    return device


def display_name(device: Any, profile: dict[str, Any]) -> str:
    """Name for a new device: one-word names like "Inverter" are ambiguous, so use
    manufacturer and model there; profiles may strip noise from names."""
    name = device.name
    for part in profile.get("name_strip", []):
        name = name.replace(part, "")
    name = name.strip()
    if len(name.split()) <= 1:
        name = " ".join(filter(None, (device.manufacturer, device.model))) or name
    return name


def _guard(request: Request, profile: dict[str, Any]) -> EepromGuard:
    limit = profile.get("eeprom_guard", {}).get(
        "max_writes_per_day", DEFAULT_MAX_EEPROM_WRITES
    )
    return EepromGuard(request.app.state.settings.data_dir, limit)


FORECAST_HOURS = 48


async def _local_tz(request: Request) -> ZoneInfo:
    config = await request.app.state.ha.command("get_config")
    return ZoneInfo(config["time_zone"])


def _slot_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=now.minute - now.minute % 15, second=0, microsecond=0)


def _replan(request: Request) -> None:
    # Every change that affects planning shows up in Plan and Steuerung right away
    if (runner := getattr(request.app.state, "runner", None)) is not None:
        runner.request_replan()
