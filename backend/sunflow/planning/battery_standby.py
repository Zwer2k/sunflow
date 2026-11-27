"""The battery's own draw (BMS, converters) learned from its energy counters."""

from datetime import timedelta
from typing import Any

from sunflow.analysis.efficiency import EfficiencyEstimate
from sunflow.config.models import SunflowConfig

PARAMETER = "standby_w"
# The estimate covers 90 days of counters: once a day is enough
REFRESH = timedelta(days=1)
# More is a split gone wrong (one large load on the battery's days), not a standby draw
MAX_STANDBY_W = 300.0
# Smaller changes are the estimate moving with each day, not worth writing the configuration
MIN_CHANGE_W = 1.0


def learns_standby(profile: dict[str, Any]) -> bool:
    """The profile's batteries take the learned standby draw."""
    return profile.get("parameters", {}).get(PARAMETER, {}).get("learn") == "standby"


def standby_of(estimate: EfficiencyEstimate | None) -> float | None:
    """Standby draw (W) of a plausible counter based estimate, else None."""
    if estimate is None or not estimate.plausible or estimate.standby_w is None:
        return None
    return round(min(max(estimate.standby_w, 0.0), MAX_STANDBY_W))


def store_learned(config: SunflowConfig, values: dict[str, float]) -> bool:
    """Put the learned values into the devices; True when one changed noticeably."""
    changed = False
    for device in config.devices:
        value = values.get(device.id)
        if value is None:
            continue
        previous = device.learned.get(PARAMETER)
        if previous is None or abs(value - previous) >= MIN_CHANGE_W:
            device.learned[PARAMETER] = value
            changed = True
    return changed
