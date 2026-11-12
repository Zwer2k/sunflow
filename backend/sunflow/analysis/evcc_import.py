"""Charging sessions from evcc, taken over once: its API (/api/sessions) or its CSV export.

evcc keeps per session start, end, loadpoint, vehicle, RFID identifier, energy, solar share, price and CO2.
Loadpoints and vehicles are matched to Sunflow's by name; the user can correct the matching.
"""

import csv
from datetime import UTC, datetime, tzinfo
import io
import json
import re
from typing import Any

import aiohttp

FETCH_TIMEOUT_S = 30
# A Go duration in JSON is nanoseconds; a number this large cannot be seconds of one session
NANOSECONDS_FROM = 1e9

# CSV headers of evcc's export in English and German (lower case, without units in brackets)
COLUMNS = {
    "start": ("created", "erstellt", "start", "beginn", "startzeit", "start time", "starttime"),
    "end": ("finished", "beendet", "ende", "end", "endzeit", "end time", "endtime"),
    "soc_start": ("ladestand start", "soc start", "start soc", "state of charge start"),
    "soc_end": ("ladestand ende", "soc end", "end soc", "state of charge end"),
    "loadpoint": ("loadpoint", "ladepunkt"),
    "identifier": ("identifier", "kennung", "id"),
    "vehicle": ("vehicle", "fahrzeug"),
    "energy": ("energy", "energie", "charged energy", "geladene energie"),
    "duration": ("charge duration", "ladedauer", "duration", "dauer"),
    "solar": ("solar", "sonne", "solar share", "sonnenanteil"),
    "price": ("price", "preis", "kosten"),
    "co2": ("co2/kwh", "co₂/kwh", "co2"),
}


class EvccImportError(Exception):
    """The data cannot be read as evcc sessions."""


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    # German "1.234,5" and English "1,234.5"
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    else:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _time(value: Any, tz: tzinfo) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    for parse in (datetime.fromisoformat, lambda v: datetime.strptime(v, "%d.%m.%Y %H:%M:%S"), lambda v: datetime.strptime(v, "%d.%m.%Y %H:%M")):
        try:
            moment = parse(text)
        except ValueError:
            continue
        # evcc's CSV shows local time without an offset
        return (moment if moment.tzinfo else moment.replace(tzinfo=tz)).astimezone(UTC)
    return None


def _duration_s(value: Any) -> float | None:
    if isinstance(value, str) and (match := re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:([\d.]+)s)?", value.strip())) and value.strip():
        hours, minutes, seconds = (float(g) if g else 0.0 for g in match.groups())
        return hours * 3600 + minutes * 60 + seconds
    if isinstance(value, str) and re.fullmatch(r"\d+:\d{2}(:\d{2})?", value.strip()):
        parts = [int(p) for p in value.strip().split(":")]
        return parts[0] * 3600 + parts[1] * 60 + (parts[2] if len(parts) > 2 else 0)
    number = _number(value)
    if number is None:
        return None
    return number / 1e9 if number >= NANOSECONDS_FROM else number


def _from_json(items: list[dict[str, Any]], tz: tzinfo) -> list[dict[str, Any]]:
    result = []
    for item in items:
        start, end = _time(item.get("created"), tz), _time(item.get("finished"), tz)
        energy = _number(item.get("chargedEnergy"))
        if start is None or energy is None:
            continue
        duration = _duration_s(item.get("chargeDuration"))
        solar = _number(item.get("solarPercentage"))
        result.append(
            {
                "id": str(item.get("id") or start.isoformat()),
                "start": start, "end": end or start, "duration_s": duration,
                "loadpoint": item.get("loadpoint") or None, "vehicle": item.get("vehicle") or None,
                "identifier": item.get("identifier") or None,
                "energy_wh": energy * 1000, "solar_share": solar / 100 if solar is not None else None,
                "cost_eur": _number(item.get("price")), "co2_g_per_kwh": _number(item.get("co2PerKWh")),
                "soc_start": _number(item.get("socStart")), "soc_end": _number(item.get("socEnd")),
            }
        )
    return result


def _header_key(header: str) -> str | None:
    name = re.sub(r"\s*[\(\[].*?[\)\]]", "", header).strip().lower()
    if "meter" in name or "zähler" in name or "odometer" in name or "kilometer" in name:
        return None
    for key, names in COLUMNS.items():
        if name in names:
            return key
    return None


def _from_csv(text: str, tz: tzinfo) -> list[dict[str, Any]]:
    dialect = csv.Sniffer().sniff(text.splitlines()[0], delimiters=",;\t") if text.strip() else csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    if len(rows) < 2:
        return []
    keys = [_header_key(h) for h in rows[0]]
    result = []
    for number, row in enumerate(rows[1:], start=1):
        values = {key: value for key, value in zip(keys, row, strict=False) if key is not None}
        start, end = _time(values.get("start"), tz), _time(values.get("end"), tz)
        energy = _number(values.get("energy"))
        if start is None or energy is None:
            continue
        solar = _number(values.get("solar"))
        result.append(
            {
                "id": f"csv-{start.isoformat()}-{values.get('loadpoint') or number}",
                "start": start, "end": end or start, "duration_s": _duration_s(values.get("duration")),
                "loadpoint": values.get("loadpoint") or None, "vehicle": values.get("vehicle") or None,
                "identifier": values.get("identifier") or None,
                "energy_wh": energy * 1000, "solar_share": solar / 100 if solar is not None else None,
                "cost_eur": _number(values.get("price")), "co2_g_per_kwh": _number(values.get("co2")),
                "soc_start": _number(values.get("soc_start")), "soc_end": _number(values.get("soc_end")),
            }
        )
    return result


def parse(content: str, tz: tzinfo) -> list[dict[str, Any]]:
    """Sessions from the answer of evcc's /api/sessions (JSON) or its CSV export; oldest first."""
    text = content.strip().lstrip("﻿")
    if not text:
        raise EvccImportError("empty")
    if text[0] in "[{":
        try:
            data = json.loads(text)
        except ValueError as err:
            raise EvccImportError(str(err)) from err
        items = data.get("result", data.get("sessions")) if isinstance(data, dict) else data
        if not isinstance(items, list):
            raise EvccImportError("no session list")
        sessions = _from_json([i for i in items if isinstance(i, dict)], tz)
    else:
        sessions = _from_csv(text, tz)
    if not sessions:
        raise EvccImportError("no sessions")
    return sorted(sessions, key=lambda s: s["start"])


async def fetch(url: str) -> str:
    """The sessions as evcc's API answers them."""
    base = url.strip().rstrip("/")
    if not re.match(r"^https?://", base):
        base = f"http://{base}"
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=FETCH_TIMEOUT_S)) as session,
            session.get(f"{base}/api/sessions") as response,
        ):
            if response.status != 200:
                raise EvccImportError(f"HTTP {response.status}")
            return await response.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise EvccImportError(str(err) or type(err).__name__) from err


def _match(name: str | None, candidates: dict[str, str]) -> str | None:
    """A Sunflow id whose name is the same as evcc's (ignoring case, spaces and "[evcc]"-like suffixes)."""
    if not name:
        return None

    def norm(text: str) -> str:
        return re.sub(r"[\W_]+", "", re.sub(r"\(evcc\)|\[evcc\]", "", text.lower()))

    wanted = norm(name)
    exact = [key for key, other in candidates.items() if norm(other) == wanted]
    if exact:
        return exact[0]
    partial = [key for key, other in candidates.items() if wanted and (wanted in norm(other) or norm(other) in wanted)]
    return partial[0] if len(partial) == 1 else None


def preview(
    sessions: list[dict[str, Any]], consumers: dict[str, str], vehicles: dict[str, str]
) -> dict[str, Any]:
    """What would be imported: counts, time range and the suggested matching of loadpoints and vehicles."""
    loadpoints = sorted({s["loadpoint"] for s in sessions if s["loadpoint"]})
    names = sorted({s["vehicle"] for s in sessions if s["vehicle"]})
    return {
        "count": len(sessions),
        "energy_wh": round(sum(s["energy_wh"] for s in sessions)),
        "first": sessions[0]["start"].isoformat(),
        "last": sessions[-1]["start"].isoformat(),
        "loadpoints": [{"name": n, "device_id": _match(n, consumers)} for n in loadpoints],
        "vehicles": [{"name": n, "vehicle_id": _match(n, vehicles)} for n in names],
    }


def to_store(
    sessions: list[dict[str, Any]],
    loadpoints: dict[str, str | None],
    vehicle_ids: dict[str, str | None],
    vehicle_names: dict[str, str],
) -> list[dict[str, Any]]:
    """Rows for the imported sessions table, with the chosen matching; a vehicle mapped shows Sunflow's name."""
    rows = []
    for s in sessions:
        device_id = loadpoints.get(s["loadpoint"] or "")
        vehicle_id = vehicle_ids.get(s["vehicle"] or "")
        rows.append(
            {
                "source": "evcc", "id": s["id"], "start": s["start"].isoformat(), "end": s["end"].isoformat(),
                "device_id": device_id, "loadpoint": s["loadpoint"],
                "vehicle_id": vehicle_id, "vehicle": vehicle_names.get(vehicle_id or "", s["vehicle"]),
                # evcc's identifier is the RFID tag as the charger reports it
                "card": s["identifier"],
                "energy_wh": s["energy_wh"], "charging_s": s["duration_s"], "solar_share": s["solar_share"],
                "cost_eur": s["cost_eur"], "co2_g_per_kwh": s["co2_g_per_kwh"],
                "soc_start": s.get("soc_start"), "soc_end": s.get("soc_end"),
            }
        )
    return rows
