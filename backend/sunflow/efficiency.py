"""Estimate the battery efficiency from measured charge, discharge and SoC.

With charged energy E_c, discharged energy E_d and the change of stored
energy dS over a period, and the same efficiency eta for both directions:

    dS = eta * E_c - E_d / eta
    eta = (dS + sqrt(dS^2 + 4 * E_c * E_d)) / (2 * E_c)

Unexplained standby consumption lowers the estimate, so it is only
offered when enough energy has cycled and the result is plausible.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math

from .ha import HAClient

MIN_CYCLES = 2.0
# Batteries with DC/DC converters in each module (e.g. Sofar BTS) lose noticeably more
PLAUSIBLE = (0.70, 0.995)


@dataclass(frozen=True, kw_only=True)
class EfficiencyEstimate:
    """Estimated one-way efficiency and how reliable it is."""

    efficiency: float | None
    round_trip: float | None
    hours: float
    charged_kwh: float
    discharged_kwh: float
    stored_change_kwh: float
    cycles: float
    reliable: bool
    plausible: bool
    # "counters" (energy counters of the inverter) or "power" (integrated power means)
    source: str = "power"


def estimate(
    battery_w: list[float], interval_h: float, soc_start_pct: float, soc_end_pct: float, capacity_wh: float
) -> EfficiencyEstimate:
    """Estimate from evenly spaced battery power means (+ = charge)."""
    return estimate_energy(
        sum(max(p, 0) for p in battery_w) * interval_h,
        sum(max(-p, 0) for p in battery_w) * interval_h,
        len(battery_w) * interval_h,
        soc_start_pct,
        soc_end_pct,
        capacity_wh,
    )


def estimate_energy(
    charged: float,
    discharged: float,
    hours: float,
    soc_start_pct: float,
    soc_end_pct: float,
    capacity_wh: float,
    source: str = "power",
) -> EfficiencyEstimate:
    """Estimate from charged and discharged energy (Wh) and the SoC change over the period."""
    stored = capacity_wh * (soc_end_pct - soc_start_pct) / 100
    eta = None
    if charged > 0:
        root = stored * stored + 4 * charged * discharged
        eta = (stored + math.sqrt(root)) / (2 * charged) if root >= 0 else None
    cycles = charged / capacity_wh if capacity_wh else 0.0
    plausible = eta is not None and PLAUSIBLE[0] <= eta <= PLAUSIBLE[1]
    return EfficiencyEstimate(
        efficiency=eta,
        round_trip=eta * eta if eta is not None else None,
        hours=hours,
        charged_kwh=charged / 1000,
        discharged_kwh=discharged / 1000,
        stored_change_kwh=stored / 1000,
        cycles=cycles,
        reliable=cycles >= MIN_CYCLES,
        plausible=plausible,
        source=source,
    )


async def estimate_from_history(
    client: HAClient, power_id: str, soc_id: str, capacity_wh: float, days: int = 10, sign: float = 1.0
) -> EfficiencyEstimate | None:
    """Estimate from Home Assistant's 5 minute statistics of the last days."""
    end = datetime.now(UTC)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=(end - timedelta(days=days)).isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[power_id, soc_id],
        period="5minute",
        types=["mean"],
        units={"power": "W"},
    )
    power = [r["mean"] * sign for r in result.get(power_id, []) if r.get("mean") is not None]
    soc = [r["mean"] for r in result.get(soc_id, []) if r.get("mean") is not None]
    if len(power) < 12 or len(soc) < 2:
        return None
    return estimate(power, 5 / 60, soc[0], soc[-1], capacity_wh)


async def estimate_from_counters(
    client: HAClient, charge_id: str, discharge_id: str, soc_id: str, capacity_wh: float, days: int = 30
) -> EfficiencyEstimate | None:
    """Estimate from the inverter's charge and discharge counters (long-term statistics)."""
    end = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    result = await client.command(
        "recorder/statistics_during_period",
        start_time=start.isoformat(),
        end_time=end.isoformat(),
        statistic_ids=[charge_id, discharge_id, soc_id],
        period="hour",
        types=["change", "mean"],
        units={"energy": "Wh"},
    )
    charge = [r for r in result.get(charge_id, []) if r.get("change") is not None]
    discharge = [r for r in result.get(discharge_id, []) if r.get("change") is not None]
    soc = [r for r in result.get(soc_id, []) if r.get("mean") is not None]
    if len(charge) < 24 or len(soc) < 2:
        return None
    # Only the hours all three statistics cover, so the SoC change matches the counted energy
    first = max(charge[0]["start"], discharge[0]["start"], soc[0]["start"])
    last = min(charge[-1]["start"], discharge[-1]["start"], soc[-1]["start"])

    def within(rows: list[dict]) -> list[dict]:
        return [r for r in rows if first <= r["start"] <= last]

    soc_rows = within(soc)
    return estimate_energy(
        sum(r["change"] for r in within(charge)),
        sum(r["change"] for r in within(discharge)),
        (last - first) / 3_600_000 + 1,
        soc_rows[0]["mean"],
        soc_rows[-1]["mean"],
        capacity_wh,
        source="counters",
    )
