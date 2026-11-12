"""Charging and running sessions of the consumers, with where their energy came from and what it cost.

Built from the 15-minute recording: in every slot the consumers' energy is split into PV (what the house
did not use itself), home battery and grid, the same way for all of them. A session of a wallbox lasts
while a car is plugged in (recorded from now on); otherwise, and for the other consumers, while it draws
power with pauses of at most an hour. Sessions imported from evcc fill the time before Sunflow recorded.

Prices as evcc counts them: grid energy at the import price of its slot, PV and battery at the feed-in it
did not earn. "Paid" is only the grid part, "saved" what the same energy from the grid would have cost more.
"""

from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, tzinfo
from typing import Any

SLOT_HOURS = 0.25
# Less energy in a slot (Wh, i.e. 200 W on average) is standby, not a session
MIN_SLOT_WH = 50.0
# A pause this long ends a session (when it is not known whether a car is still plugged in)
MAX_GAP = timedelta(hours=1)
# Shorter sessions are noise (a test, a few minutes of top-up)
MIN_SESSION_WH = 100.0


@dataclass(kw_only=True)
class Session:
    """One charging or running session of a consumer."""

    source: str
    device_id: str | None
    consumer: str
    start: datetime
    end: datetime
    energy_wh: float = 0.0
    pv_wh: float = 0.0
    battery_wh: float = 0.0
    # Of the battery's part what it had stored from PV (the rest it had charged from the grid)
    battery_solar_wh: float = 0.0
    grid_wh: float = 0.0
    # Grid energy at its price (what was paid) and PV/battery at the feed-in not earned
    paid_eur: float = 0.0
    cost_eur: float = 0.0
    # The same energy all from the grid at the prices of its slots
    grid_only_eur: float = 0.0
    max_power_w: float = 0.0
    vehicle_id: str | None = None
    vehicle: str | None = None
    card: str | None = None
    soc_start: float | None = None
    soc_end: float | None = None
    # Imported: the other system's own solar share (PV and battery together)
    solar_share: float | None = None
    # Hours it actually drew power (a car may stay plugged in long after)
    charging_h: float = 0.0
    # What the grid part cost is known (own recording; evcc only knows the total with the feed-in)
    paid_known: bool = True

    @property
    def duration_h(self) -> float:
        """Hours from start to end."""
        return (self.end - self.start).total_seconds() / 3600

    def as_dict(self) -> dict[str, Any]:
        """For the API."""
        result = asdict(self) | {"duration_h": round(self.duration_h, 2)}
        result["start"], result["end"] = self.start.isoformat(), self.end.isoformat()
        result["solar_share"] = round(self.share(), 4) if self.energy_wh else None
        for key in ("energy_wh", "pv_wh", "battery_wh", "battery_solar_wh", "grid_wh", "max_power_w"):
            result[key] = round(result[key], 1)
        for key in ("paid_eur", "cost_eur", "grid_only_eur"):
            result[key] = round(result[key], 4)
        return result

    def share(self) -> float:
        """Part of the energy from own production (PV and home battery), as evcc's solar share."""
        if self.solar_share is not None:
            return self.solar_share
        return (self.pv_wh + self.battery_solar_wh) / self.energy_wh if self.energy_wh else 0.0


def split_slot(row: dict[str, Any]) -> dict[str, tuple[float, float, float]]:
    """Per consumer the energy (Wh) of a slot from PV, home battery and grid.

    The house's own load takes the PV first, then the battery; what is left of both goes to the consumers
    in proportion to their power, the rest comes from the grid.
    """
    consumers = {key: max(power or 0.0, 0.0) for key, power in (row.get("actual_consumers") or {}).items()}
    total = sum(consumers.values())
    if total <= 0:
        return {}
    load = row.get("actual_load_w")
    base = max((load if load is not None else total) - total, 0.0)
    pv = max(row.get("actual_pv_w") or 0.0, 0.0)
    discharge = max(-(row.get("actual_battery_w") or 0.0), 0.0)
    pv_left = max(pv - base, 0.0)
    battery_left = max(discharge - max(base - pv, 0.0), 0.0)
    pv_used = min(pv_left, total)
    battery_used = min(battery_left, total - pv_used)
    result = {}
    for key, power in consumers.items():
        part = power / total
        from_pv, from_battery = pv_used * part, battery_used * part
        result[key] = (from_pv * SLOT_HOURS, from_battery * SLOT_HOURS, (power - from_pv - from_battery) * SLOT_HOURS)
    return result


@dataclass(kw_only=True)
class BatteryContent:
    """What the home battery holds: how much of it came from PV, and what its grid part cost per kWh.

    Charging adds PV surplus and grid energy at their shares; discharging takes from the mix. Before
    the first charge it is unknown and counts half and half.
    """

    stored_wh: float = 0.0
    solar: float = 0.5
    grid_price: float | None = None

    def step(self, row: dict[str, Any]) -> None:
        """Account for one slot's charging or discharging."""
        battery = row.get("actual_battery_w") or 0.0
        if battery > 0:
            charged = battery * SLOT_HOURS
            surplus = max((row.get("actual_pv_w") or 0.0) - (row.get("actual_load_w") or 0.0), 0.0) * SLOT_HOURS
            from_pv = min(charged, surplus)
            from_grid = charged - from_pv
            total = self.stored_wh + charged
            grid_before = self.stored_wh * (1 - self.solar)
            if from_grid > 0 and (price := row.get("planned_price")) is not None:
                known = self.grid_price if self.grid_price is not None else price
                self.grid_price = (known * grid_before + price * from_grid) / (grid_before + from_grid)
            self.solar = (self.stored_wh * self.solar + from_pv) / total if total > 0 else self.solar
            self.stored_wh = total
        elif battery < 0:
            self.stored_wh = max(self.stored_wh + battery * SLOT_HOURS, 0.0)
        # The measured content keeps the sum from drifting (losses, standby)
        if (soc := row.get("actual_soc_wh")) is not None:
            self.stored_wh = soc


@dataclass(kw_only=True)
class _Open:
    session: Session
    # Last slot it drew power (or a car was plugged in)
    last: datetime
    # Started with a car known to be plugged in: it ends with unplugging
    plugged: bool


def sessions_from_rows(
    rows: list[dict[str, Any]],
    names: dict[str, str],
    vehicles: dict[str, str],
    card_labels: dict[tuple[str, int], str],
    feed_in: float,
) -> list[Session]:
    """Sessions of every consumer in the recorded slots (oldest first).

    names: consumer device id -> name; vehicles: vehicle id -> name; card_labels: (wallbox, number) -> label;
    feed_in: € per kWh that PV and battery energy would have earned.
    """
    open_: dict[str, _Open] = {}
    done: list[Session] = []

    def close(key: str) -> None:
        if (current := open_.pop(key, None)) is not None and current.session.energy_wh >= MIN_SESSION_WH:
            done.append(current.session)

    content = BatteryContent()
    for row in rows:
        start = datetime.fromisoformat(row["start"])
        end = start + timedelta(hours=SLOT_HOURS)
        price = row.get("planned_price")
        plugged = row.get("actual_vehicles") or {}
        levels = row.get("actual_levels") or {}
        parts = split_slot(row)
        # What the battery gives in this slot is what it held before it
        battery_solar, battery_price = content.solar, content.grid_price if content.grid_price is not None else price
        content.step(row)
        for key in set(parts) | set(plugged):
            if key not in names:
                continue
            pv_wh, battery_wh, grid_wh = parts.get(key, (0.0, 0.0, 0.0))
            energy = pv_wh + battery_wh + grid_wh
            car = plugged.get(key)
            running = car is not None or energy >= MIN_SLOT_WH
            current = open_.get(key)
            # A known plug-in ends with unplugging or another car; without one a long pause ends it
            if current is not None and (
                (current.plugged and (car is None or car.get("vehicle") != current.session.vehicle_id))
                or (not current.plugged and (car is not None or start - current.last > MAX_GAP))
            ):
                close(key)
                current = None
            if not running:
                continue
            if current is None:
                vehicle_id = car.get("vehicle") if car else None
                card = car.get("card") if car else None
                current = open_[key] = _Open(
                    session=Session(
                        source="sunflow", device_id=key, consumer=names[key], start=start, end=end,
                        vehicle_id=vehicle_id, vehicle=vehicles.get(vehicle_id or ""),
                        card=card_labels.get((key, card)) if card else None,
                        soc_start=levels.get(f"vehicle:{vehicle_id}") if vehicle_id else None,
                    ),
                    last=start,
                    plugged=car is not None,
                )
            s = current.session
            if energy >= MIN_SLOT_WH or car is not None:
                current.last = start
                s.end = end
            if energy >= MIN_SLOT_WH:
                s.charging_h += SLOT_HOURS
            s.energy_wh += energy
            s.pv_wh += pv_wh
            s.battery_wh += battery_wh
            s.battery_solar_wh += battery_wh * battery_solar
            s.grid_wh += grid_wh
            s.max_power_w = max(s.max_power_w, energy / SLOT_HOURS)
            # The battery's grid part was paid when it was charged, at that price
            if battery_price is not None:
                s.paid_eur += battery_wh * (1 - battery_solar) / 1000 * battery_price
            if price is not None:
                s.paid_eur += grid_wh / 1000 * price
                s.grid_only_eur += energy / 1000 * price
            s.cost_eur = s.paid_eur + (s.pv_wh + s.battery_solar_wh) / 1000 * feed_in
            if s.vehicle_id and (soc := levels.get(f"vehicle:{s.vehicle_id}")) is not None:
                s.soc_end = soc
    for key in list(open_):
        close(key)
    return sorted(done, key=lambda s: s.start)


def imported(rows: list[dict[str, Any]], names: dict[str, str]) -> list[Session]:
    """Sessions stored from an import (evcc)."""
    result = []
    for row in rows:
        energy = row["energy_wh"] or 0.0
        share = row["solar_share"]
        own = energy * share if share is not None else 0.0
        result.append(
            Session(
                source=row["source"], device_id=row["device_id"],
                consumer=names.get(row["device_id"] or "", row["loadpoint"] or "?"),
                start=datetime.fromisoformat(row["start"]), end=datetime.fromisoformat(row["end"]),
                energy_wh=energy, pv_wh=own, grid_wh=energy - own, cost_eur=row["cost_eur"] or 0.0,
                vehicle_id=row["vehicle_id"], vehicle=row["vehicle"], card=row["card"], solar_share=share,
                charging_h=(row.get("charging_s") or 0.0) / 3600, paid_known=False,
                soc_start=row.get("soc_start"), soc_end=row.get("soc_end"),
            )
        )
    return result


def merge(own: list[Session], others: list[Session]) -> list[Session]:
    """Own sessions, and imported ones only before Sunflow recorded the consumer (no double counting)."""
    first: dict[str | None, datetime] = {}
    for s in own:
        first[s.device_id] = min(first.get(s.device_id, s.start), s.start)
    # A loadpoint not mapped to a device: up to the first own session of any consumer
    earliest = min(first.values(), default=None)
    kept = [
        s
        for s in others
        if (limit := first.get(s.device_id) if s.device_id else earliest) is None or s.start < limit
    ]
    return sorted([*own, *kept], key=lambda s: s.start)


@dataclass(kw_only=True)
class Totals:
    """Sums over sessions."""

    sessions: int = 0
    energy_wh: float = 0.0
    pv_wh: float = 0.0
    battery_wh: float = 0.0
    battery_solar_wh: float = 0.0
    grid_wh: float = 0.0
    paid_eur: float = 0.0
    cost_eur: float = 0.0
    grid_only_eur: float = 0.0
    hours: float = 0.0
    # Energy and hours of the sessions whose charging time is known
    charging_h: float = 0.0
    timed_wh: float = 0.0
    own_wh: float = 0.0
    priced_wh: float = 0.0
    # Sessions whose paid grid part is known
    paid_sessions: int = 0
    by: dict[str, Any] = field(default_factory=dict)

    def add(self, s: Session) -> None:
        """Count one session."""
        self.sessions += 1
        self.energy_wh += s.energy_wh
        self.pv_wh += s.pv_wh
        self.battery_wh += s.battery_wh
        self.battery_solar_wh += s.battery_solar_wh
        self.grid_wh += s.grid_wh
        if s.paid_known:
            self.paid_sessions += 1
            self.paid_eur += s.paid_eur
            self.grid_only_eur += s.grid_only_eur
        self.cost_eur += s.cost_eur
        self.hours += s.duration_h
        if s.charging_h:
            self.charging_h += s.charging_h
            self.timed_wh += s.energy_wh
        self.own_wh += s.share() * s.energy_wh
        if s.cost_eur or s.grid_only_eur:
            self.priced_wh += s.energy_wh

    def as_dict(self) -> dict[str, Any]:
        """For the API, with the derived figures."""
        return {
            "sessions": self.sessions,
            "energy_wh": round(self.energy_wh, 1),
            "pv_wh": round(self.pv_wh, 1),
            "battery_wh": round(self.battery_wh, 1),
            "battery_solar_wh": round(self.battery_solar_wh, 1),
            "grid_wh": round(self.grid_wh, 1),
            "solar_share": round(self.own_wh / self.energy_wh, 4) if self.energy_wh else None,
            # Only of the own recording; imported sessions know only their total cost
            "paid_eur": round(self.paid_eur, 2) if self.paid_sessions else None,
            "paid_sessions": self.paid_sessions,
            "cost_eur": round(self.cost_eur, 2),
            "saved_eur": round(self.grid_only_eur - self.paid_eur, 2) if self.grid_only_eur else None,
            "price_per_kwh": round(self.cost_eur / (self.priced_wh / 1000), 4) if self.priced_wh else None,
            "hours": round(self.charging_h, 1),
            # While it draws power, not while it only stays plugged in
            "mean_power_w": round(self.timed_wh / self.charging_h) if self.charging_h else None,
        }


def statistics(
    sessions: list[Session], since: datetime | None, tz: tzinfo, device_id: str | None = None, vehicle_id: str | None = None
) -> dict[str, Any]:
    """Totals, per month, per consumer and per vehicle, and the sessions (newest first) of a period."""
    chosen = [
        s
        for s in sessions
        if (since is None or s.start >= since)
        and (device_id is None or s.device_id == device_id)
        and (vehicle_id is None or s.vehicle_id == vehicle_id)
    ]
    total = Totals()
    months: dict[str, Totals] = defaultdict(Totals)
    consumers: dict[str, Totals] = defaultdict(Totals)
    by_vehicle: dict[str, Totals] = defaultdict(Totals)
    for s in chosen:
        total.add(s)
        months[s.start.astimezone(tz).strftime("%Y-%m")].add(s)
        consumers[s.consumer].add(s)
        if s.vehicle:
            by_vehicle[s.vehicle].add(s)
    return {
        "totals": total.as_dict(),
        "months": [{"month": m, **months[m].as_dict()} for m in _month_range(months)],
        "consumers": [{"name": n, **t.as_dict()} for n, t in sorted(consumers.items(), key=lambda i: -i[1].energy_wh)],
        "vehicles": [{"name": n, **t.as_dict()} for n, t in sorted(by_vehicle.items(), key=lambda i: -i[1].energy_wh)],
        "sessions": [s.as_dict() for s in reversed(chosen)],
        "first": min((s.start for s in sessions), default=None),
    }


def _month_range(months: dict[str, Totals]) -> list[str]:
    """Every month from the first to the last with sessions, the empty ones too (a gap stays visible)."""
    if not months:
        return []
    first, last = min(months), max(months)
    year, month = int(first[:4]), int(first[5:])
    result = []
    while (key := f"{year:04d}-{month:02d}") <= last:
        result.append(key)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result
