"""Statistics of the consumers' sessions (charging, heating) and the one-time import from evcc."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from sunflow.analysis import evcc_import
from sunflow.analysis.consumer_stats import (
    imported,
    merge,
    sessions_from_rows,
    statistics,
)
from sunflow.config.models import SunflowConfig
from sunflow.i18n import t

from .common import _fresh_cache, _local_tz, kept_fresh

router = APIRouter()

# Sessions are rebuilt from the whole recording at most this often (a new slot every 15 minutes)
STATS_MAX_AGE = timedelta(minutes=5)
# Recording read for the sessions (all of it; the database keeps what it has)
RECORDING_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
# Largest import text accepted (a CSV of years of sessions is far below)
IMPORT_MAX_CHARS = 20_000_000

Period = Literal["30d", "365d", "this_year", "last_year", "all"]


def _consumers(config: SunflowConfig) -> dict[str, str]:
    """Devices that are consumers (everything that is planned as a flexible load or measured as one)."""
    return {d.id: d.name for d in [*config.devices, *config.disabled_devices]}


def _feed_in_eur(config: SunflowConfig) -> float:
    """€ per kWh own energy would have earned: the mean of the configured fixed rates."""
    rates = [f.rate_ct_per_kwh for f in config.feed_in.values() if f.rate_ct_per_kwh is not None]
    return sum(rates) / len(rates) / 100 if rates else 0.0


def _period_start(period: Period, now: datetime, tz: ZoneInfo) -> tuple[datetime | None, datetime | None]:
    local = now.astimezone(tz)
    if period == "30d":
        return now - timedelta(days=30), None
    if period == "365d":
        return now - timedelta(days=365), None
    if period == "this_year":
        return local.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0), None
    if period == "last_year":
        start = local.replace(year=local.year - 1, month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, start.replace(year=local.year)
    return None, None


async def _sessions(request: Request) -> list[Any]:
    """Own sessions from the recording and the imported ones before them."""
    runner = request.app.state.runner
    config: SunflowConfig = request.app.state.store.load()
    names = _consumers(config)
    vehicles = {v.id: v.name for v in config.vehicles}
    labels = {
        (p.wallbox_id, c.number): c.name or t("Karte {number}", number=c.number) for p in config.charging for c in p.cards
    }

    def build() -> list[Any]:
        rows = runner.shadow.rows(RECORDING_SINCE)
        own = sessions_from_rows(rows, names, vehicles, labels, _feed_in_eur(config))
        return merge(own, imported(runner.shadow.imported_sessions(), names))

    async def compute() -> list[Any]:
        return await asyncio.to_thread(build)

    return await kept_fresh(request, "consumer_sessions", STATS_MAX_AGE, compute)


@router.get("/consumer-stats")
async def get_consumer_stats(
    request: Request, period: Period = "365d", device_id: str | None = None, vehicle_id: str | None = None
) -> dict[str, Any]:
    """Sessions of the consumers in a period with totals per month, consumer and vehicle (like evcc's statistics)."""
    tz = await _local_tz(request)
    since, until = _period_start(period, datetime.now(UTC), tz)
    sessions = await _sessions(request)
    if until is not None:
        sessions = [s for s in sessions if s.start < until]
    result = statistics(sessions, since, tz, device_id, vehicle_id)
    config = request.app.state.store.load()
    used = {s.device_id for s in sessions}
    profiles = request.app.state.profiles.profiles
    loads = {
        d.id: d.name
        for d in config.devices
        if {"loadpoint", "load"} & set(profiles.get(d.profile, {}).get("capabilities", []))
    }
    result["filters"] = {
        "consumers": [{"id": key, "name": name} for key, name in _consumers(config).items() if key in used],
        # For matching evcc's loadpoints: every loadpoint and load
        "all_consumers": [{"id": key, "name": name} for key, name in loads.items()],
        "vehicles": [{"id": v.id, "name": v.name} for v in config.vehicles],
    }
    result["imported"] = sum(1 for s in sessions if s.source != "sunflow")
    result["feed_in_ct"] = round(_feed_in_eur(config) * 100, 2)
    result["first"] = result["first"].isoformat() if result["first"] else None
    return result


class EvccSource(BaseModel):
    """Where the evcc sessions come from: its address, or the text of its API answer or CSV export."""

    url: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=IMPORT_MAX_CHARS)


class EvccImport(EvccSource):
    """The import with the matching chosen in the preview: evcc name -> Sunflow id (None: not matched)."""

    loadpoints: dict[str, str | None] = Field(default_factory=dict)
    vehicles: dict[str, str | None] = Field(default_factory=dict)


async def _read(request: Request, source: EvccSource) -> list[dict[str, Any]]:
    tz = await _local_tz(request)
    try:
        if source.content:
            text = source.content
        elif source.url:
            text = await evcc_import.fetch(source.url)
        else:
            raise HTTPException(status_code=422, detail=t("Adresse von evcc oder eine Datei angeben"))
        return evcc_import.parse(text, tz)
    except evcc_import.EvccImportError as err:
        raise HTTPException(status_code=422, detail=t("Keine Ladevorgänge von evcc gelesen: {error}", error=str(err))) from err


@router.post("/consumer-stats/import/evcc/preview")
async def preview_evcc(request: Request, body: EvccSource) -> dict[str, Any]:
    """What an import would take over, with the suggested matching of loadpoints and vehicles."""
    sessions = await _read(request, body)
    config = request.app.state.store.load()
    result = evcc_import.preview(sessions, _consumers(config), {v.id: v.name for v in config.vehicles})
    result["already"] = len(request.app.state.runner.shadow.imported_sessions())
    return result


@router.post("/consumer-stats/import/evcc")
async def import_evcc(request: Request, body: EvccImport) -> dict[str, Any]:
    """Take over evcc's sessions once; sessions imported before (same id) are kept, not doubled."""
    sessions = await _read(request, body)
    config = request.app.state.store.load()
    consumers = _consumers(config)
    vehicles = {v.id: v.name for v in config.vehicles}
    if any(d is not None and d not in consumers for d in body.loadpoints.values()) or any(
        v is not None and v not in vehicles for v in body.vehicles.values()
    ):
        raise HTTPException(status_code=422, detail="Unknown device or vehicle")
    rows = evcc_import.to_store(sessions, body.loadpoints, body.vehicles, vehicles)
    added = await asyncio.to_thread(request.app.state.runner.shadow.store_imported_sessions, rows)
    _forget_sessions(request)
    return {"imported": added, "skipped": len(rows) - added}


@router.delete("/consumer-stats/import/evcc")
async def remove_evcc(request: Request) -> dict[str, Any]:
    """Forget the sessions taken over from evcc (e.g. to import again with another matching)."""
    removed = await asyncio.to_thread(request.app.state.runner.shadow.remove_imported_sessions, "evcc")
    _forget_sessions(request)
    return {"removed": removed}


def _forget_sessions(request: Request) -> None:
    """The next statistics request builds the sessions anew."""
    _fresh_cache(request.app.state)[0].pop("consumer_sessions", None)
