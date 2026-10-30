"""Helpers shared by the API routers."""

from datetime import UTC, datetime
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


router = APIRouter(prefix=f"/api/{API_VERSION}")


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
