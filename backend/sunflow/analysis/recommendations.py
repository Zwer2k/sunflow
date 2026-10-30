"""Recommendations: settings that the measured data or the plan suggest to change.

Each check looks at one aspect and returns at most one recommendation. A
recommendation has a stable id plus a fingerprint of its numbers: dismissing it
hides it until the numbers change noticeably.
"""

from dataclasses import asdict, dataclass, field
import hashlib
from typing import Any

from sunflow.config.models import BATTERY_PRIORITY_ID, SunflowConfig
from sunflow.planning.plan import PlanResult
from sunflow.planning.pv_learning import PvCorrection

from .efficiency import EfficiencyEstimate

# Shadow data needed before load or PV deviations count
MIN_SHADOW_DAYS = 3
# Battery efficiency off by more than this (percentage points) from the counters
EFFICIENCY_GAP = 3.0
# Hot water reheated per day above this temperature drop of the tank looks implausible
MAX_DAILY_DROP_K = 20
WATER_WH_PER_LITRE_KELVIN = 1.163


@dataclass(kw_only=True)
class Recommendation:
    """One suggestion; action, when present, can be applied by the UI with one click."""

    id: str
    severity: str  # "warn" or "info"
    title: str
    text: str
    action: dict[str, Any] | None = None
    fingerprint: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _fingerprint(*values: Any) -> str:
    """Rounded numbers, so small fluctuations do not bring a dismissed hint back."""
    text = "|".join(f"{v:.0f}" if isinstance(v, float) else str(v) for v in values)
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def _pct(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + " %"


def battery_efficiency(
    config: SunflowConfig, estimates: dict[str, EfficiencyEstimate | None]
) -> list[Recommendation]:
    """The battery efficiency differs from what the energy counters show."""
    result = []
    for device in config.devices:
        estimate = estimates.get(device.id)
        current = device.parameters.get("efficiency_pct")
        # Only the counter based split: the overall round trip includes the standby draw and
        # would alternate with it by several points whenever the split is not possible
        if estimate is None or current is None or not estimate.plausible or estimate.conversion_round_trip is None:
            continue
        # Half points: the estimate moves a little with every day of data
        suggested = round(estimate.conversion_round_trip * 200) / 2
        if abs(suggested - current) < EFFICIENCY_GAP:
            continue
        standby = (
            f" Der Eigenverbrauch der Batterie (etwa {estimate.standby_w:.0f} W) ist darin nicht enthalten – "
            "er hängt nicht vom Laden ab."
            if estimate.standby_w
            else ""
        )
        result.append(
            Recommendation(
                id=f"battery_efficiency:{device.id}",
                severity="warn",
                title=f"{device.name}: Wirkungsgrad {_pct(suggested)} statt {_pct(current)}",
                text=(
                    f"Die Energiezähler zeigen für den Speicher {_pct(suggested)} im Zyklus "
                    f"({estimate.cycles:.0f} Zyklen ausgewertet).{standby} Ein zu niedriger Wert lässt den Speicher "
                    "schlechter aussehen, als er ist."
                ),
                action={"type": "set_parameter", "device_id": device.id, "name": "efficiency_pct", "value": round(suggested, 1)},
                fingerprint=_fingerprint(round(suggested), round(current)),
            )
        )
    return result


def missing_settings(config: SunflowConfig, latest: PlanResult | None) -> list[Recommendation]:
    """Consumers that cannot be planned for lack of a value."""
    result = []
    for info in latest.flex if latest else []:
        if info.reason == "Energiebedarf pro Tag fehlt":
            result.append(
                Recommendation(
                    id=f"daily_energy:{info.device_id}",
                    severity="warn",
                    title=f"{info.name}: Energiebedarf pro Tag fehlt",
                    text="Ohne Tagesbedarf plant Sunflow den Verbraucher nicht ein. Trage ein, wie viel er an einem "
                    "typischen Tag braucht (Geräte → Eigene Verbraucher → Bearbeiten).",
                    action={"type": "navigate", "view": "setup", "step": 1},
                )
            )
    for device in config.devices:
        if device.parameters.get("tank_l") and device.flex.alternative_cost_ct is None:
            result.append(
                Recommendation(
                    id=f"alternative_cost:{device.id}",
                    severity="warn",
                    title=f"{device.name}: Preis der üblichen Heizung fehlt",
                    text="Ohne ihn behandelt Sunflow das Warmwasser als Pflicht und heizt im Zweifel mit Netzstrom. "
                    "Trage den Preis z. B. der Fernwärme in ct/kWh ein (Kenndaten → Planung).",
                    action={"type": "navigate", "view": "setup", "step": 2},
                )
            )
    return result


def hot_water_demand(config: SunflowConfig) -> list[Recommendation]:
    """A daily hot water demand that would mean a very large temperature drop."""
    result = []
    for device in config.devices:
        litres, daily = device.parameters.get("tank_l"), device.parameters.get("daily_energy_wh")
        if not litres or not daily:
            continue
        drop = daily / (litres * WATER_WH_PER_LITRE_KELVIN)
        if drop > MAX_DAILY_DROP_K:
            result.append(
                Recommendation(
                    id=f"hot_water_demand:{device.id}",
                    severity="info",
                    title=f"{device.name}: {daily / 1000:.1f} kWh pro Tag prüfen".replace(".", ","),
                    text=(
                        f"Das entspricht einer Abkühlung der {litres:.0f} l um etwa {drop:.0f} K pro Tag. "
                        "Ist der Verbrauch geringer, plant Sunflow zu viel Warmwasser ein."
                    ),
                    action={"type": "navigate", "view": "setup", "step": 2},
                    fingerprint=_fingerprint(daily, litres),
                )
            )
    return result


def limits_cost_pv(config: SunflowConfig, latest: PlanResult | None) -> list[Recommendation]:
    """A priority limit on the battery keeps PV from a consumer that then uses its alternative."""
    if latest is None:
        return []
    result = []
    results = {r.name: r for r in latest.plan.flex}
    for info in latest.flex:
        device = config.device(info.device_id)
        planned = results.get(info.name)
        if device is None or planned is None or device.flex.alternative_cost_ct is None:
            continue
        limit = next(
            (c for c in config.valid_conditions(device.id) if c.device_id == BATTERY_PRIORITY_ID and c.min_pct >= 80),
            None,
        )
        if limit is None or planned.unmet_wh < 2000:
            continue
        result.append(
            Recommendation(
                id=f"limit_blocks_pv:{device.id}",
                severity="info",
                title=f"{device.name}: Grenze „ab Hausspeicher {limit.min_pct:.0f} %“ kostet PV".replace(".", ","),
                text=(
                    f"Im aktuellen Plan deckt die übliche Heizung {planned.unmet_wh / 1000:.1f} kWh, weil "
                    f"{device.name} erst ab {limit.min_pct:.0f} % Hausspeicher PV bekommt. An Tagen, an denen der "
                    "Speicher diesen Stand nicht erreicht (Winter), geht so keine PV ins Warmwasser. Eine niedrigere "
                    "Grenze (z. B. 60 %) nutzt mehr PV direkt."
                ).replace(".", ",", 1),
                action={"type": "navigate", "view": "control"},
                fingerprint=_fingerprint(limit.min_pct, round(planned.unmet_wh / 1000)),
            )
        )
    return result


def plan_worse_than_baseline(latest: PlanResult | None) -> list[Recommendation]:
    """The plan costs more than simple self-consumption; usually settings force it."""
    if latest is None or not latest.plan.slots:
        return []
    savings = latest.plan.savings
    if savings > -0.2:
        return []
    return [
        Recommendation(
            id="plan_worse",
            severity="info",
            title=f"Plan ist {abs(savings):.2f} € teurer als reiner Eigenverbrauch".replace(".", ","),
            text=(
                "Das liegt meist an Vorgaben: Grenzen in der Priorität, „Nur PV“ ohne Speicher, Ladegrenze oder "
                "Vollladung. Sunflow hält sich daran, auch wenn es Geld kostet. Prüfe die Grenzen unter Steuerung."
            ),
            action={"type": "navigate", "view": "control"},
            fingerprint=_fingerprint(round(savings, 1)),
        )
    ]


# Learned and set consumer values differing by more than this share
USAGE_DEVIATION = 0.3


def consumer_usage(config: SunflowConfig, latest: PlanResult | None) -> list[Recommendation]:
    """Own consumers whose measured use differs from the set daily energy or power."""
    result = []
    for device_id, usage in (latest.usages if latest else {}).items():
        device = config.device(device_id)
        if device is None:
            continue
        daily = device.parameters.get("daily_energy_wh")
        if daily is None or abs(usage.daily_wh / daily - 1) > USAGE_DEVIATION:
            result.append(
                Recommendation(
                    id=f"consumer_daily:{device_id}",
                    severity="info",
                    title=f"{device.name}: gemessen {usage.daily_wh / 1000:.2f} kWh pro Tag".replace(".", ","),
                    text=(
                        f"Aus {usage.days} Tagen Leistungsmessung. "
                        + (
                            f"Eingestellt sind {daily / 1000:.2f} kWh.".replace(".", ",")
                            if daily is not None
                            else "Eingestellt ist nichts – die Planung nutzt bereits den gemessenen Wert."
                        )
                    ),
                    action={"type": "set_parameter", "device_id": device_id, "name": "daily_energy_wh", "value": round(usage.daily_wh)},
                    fingerprint=_fingerprint(round(usage.daily_wh / 100), daily),
                )
            )
        power = device.parameters.get("max_power")
        if usage.running_w and power and abs(usage.running_w / power - 1) > USAGE_DEVIATION:
            result.append(
                Recommendation(
                    id=f"consumer_power:{device_id}",
                    severity="info",
                    title=f"{device.name}: läuft mit etwa {usage.running_w:.0f} W statt {power:.0f} W",
                    text="Die Planung reserviert die eingestellte Leistung; mit der gemessenen passt sie besser in den PV-Überschuss.",
                    action={"type": "set_parameter", "device_id": device_id, "name": "max_power", "value": round(usage.running_w)},
                    fingerprint=_fingerprint(round(usage.running_w / 50), power),
                )
            )
    return result


def pv_forecast(config: SunflowConfig, correction: PvCorrection | None, weather: list[dict[str, Any]]) -> list[Recommendation]:
    """PV forecast far off while learning is off, or no weather with cloud coverage."""
    result = []
    if correction and correction.days >= MIN_SHADOW_DAYS and not config.optimizer.pv_learning:
        if abs(correction.overall - 1) > 0.15:
            result.append(
                Recommendation(
                    id="pv_learning_off",
                    severity="warn",
                    title=f"PV-Prognose liegt bei {correction.overall * 100:.0f} % der Messung",
                    text="Das Anpassen der PV-Prognose ist ausgeschaltet. Eingeschaltet plant Sunflow mit dem, "
                    "was deine Anlage tatsächlich liefert.",
                    action={"type": "set_optimizer", "changes": {"pv_learning": True}},
                    fingerprint=_fingerprint(round(correction.overall * 100)),
                )
            )
    if weather and not any(w.get("cloud_coverage") for w in weather):
        result.append(
            Recommendation(
                id="weather_without_clouds",
                severity="info",
                title="Keine Wetter-Entität mit Bewölkung",
                text="Mit stündlicher Bewölkung lernt Sunflow, wie stark die PV-Prognose bei Wolken danebenliegt. "
                "Open-Meteo (in Home Assistant enthalten) liefert sie.",
            )
        )
    return result


def build(
    config: SunflowConfig,
    latest: PlanResult | None,
    estimates: dict[str, EfficiencyEstimate | None],
    shadow_rows: list[dict[str, Any]],
    shadow_days: int,
    correction: PvCorrection | None,
    weather: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """All recommendations that are not dismissed, warnings first."""
    found = [
        *battery_efficiency(config, estimates),
        *missing_settings(config, latest),
        *limits_cost_pv(config, latest),
        *plan_worse_than_baseline(latest),
        *hot_water_demand(config),
        *consumer_usage(config, latest),
        *pv_forecast(config, correction, weather),
    ]
    dismissed = set(config.dismissed_recommendations)
    visible = [r for r in found if f"{r.id}#{r.fingerprint}" not in dismissed]
    visible.sort(key=lambda r: r.severity != "warn")
    return [asdict(r) for r in visible]
