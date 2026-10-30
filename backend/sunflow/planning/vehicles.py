"""Which vehicle is connected to a wallbox.

Tried in this order, each switchable per wallbox: the RFID card scanned at the
charger, the choice in the control tab, a sensor of the car's own integration
("plugged in" / "at home"), and the only vehicle assigned to the wallbox. A card
that belongs to no assigned vehicle means a foreign car: it stays unknown, and the
wallbox regulates itself.
"""

from dataclasses import dataclass
import re
from typing import Any

from sunflow.config.models import DeviceConfig, SunflowConfig, Vehicle

# States of a car's sensor that mean it is plugged in or at home
PLUGGED = {"on", "true", "home", "connected", "plugged", "plugged_in", "charging"}
# go-e transaction options: "card_0" is card 1
CARD_OPTION = re.compile(r"^card_(\d+)$")

METHODS = {"card": "Karte", "manual": "Auswahl", "sensor": "Sensor des Fahrzeugs", "single": "einziges Fahrzeug"}


@dataclass(frozen=True, kw_only=True)
class Recognized:
    """The vehicle at a wallbox and how it was recognized; vehicle None when unknown."""

    vehicle: Vehicle | None
    method: str | None
    reason: str | None = None


def scanned_card(transaction: str | None) -> int | None:
    """Card number (1..) of the running transaction, None without a card."""
    if transaction and (match := CARD_OPTION.match(transaction)):
        return int(match[1]) + 1
    return None


def recognize(
    config: SunflowConfig, wallbox: DeviceConfig, roles: dict[str, Any], states: dict[str, dict[str, Any]]
) -> Recognized:
    """The vehicle connected to the wallbox, from its roles (transaction) and the cars' sensors."""
    assigned = config.vehicles_at(wallbox.id)
    detection = config.detection_of(wallbox.id)
    transaction = roles["transaction"].raw_state if "transaction" in roles else None
    if detection.card and (card := scanned_card(transaction)) is not None:
        cards = {c.vehicle_id: c.card for c in config.charging if c.wallbox_id == wallbox.id}
        vehicle = next((v for v in assigned if cards.get(v.id) == card), None)
        if vehicle is None:
            return Recognized(vehicle=None, method=None, reason=f"Karte {card} gehört zu keinem Fahrzeug dieser Wallbox")
        return Recognized(vehicle=vehicle, method="card")
    if detection.manual and (chosen := config.selected_vehicles.get(wallbox.id)):
        if (vehicle := next((v for v in assigned if v.id == chosen), None)) is not None:
            return Recognized(vehicle=vehicle, method="manual")
    if detection.sensor:
        plugged = [
            v
            for v in assigned
            if v.plugged_entity and str(states.get(v.plugged_entity, {}).get("state", "")).lower() in PLUGGED
        ]
        if len(plugged) == 1:
            return Recognized(vehicle=plugged[0], method="sensor")
    if detection.single and len(assigned) == 1:
        return Recognized(vehicle=assigned[0], method="single")
    return Recognized(vehicle=None, method=None, reason="Fahrzeug nicht erkannt")


def soc_of(vehicle: Vehicle, states: dict[str, dict[str, Any]]) -> float | None:
    """The vehicle's state of charge in %, None while unreadable."""
    try:
        return float(states.get(vehicle.soc_entity or "", {}).get("state"))
    except TypeError, ValueError:
        return None


def vehicle_at(
    config: SunflowConfig, wallbox: DeviceConfig, roles: dict[str, Any], states: dict[str, dict[str, Any]]
) -> tuple[Recognized, float | None]:
    """The vehicle recognized at a wallbox with assigned vehicles, and its SoC from the car's own sensor."""
    if not config.vehicles_at(wallbox.id):
        return Recognized(vehicle=None, method=None), None
    recognized = recognize(config, wallbox, roles, states)
    return recognized, soc_of(recognized.vehicle, states) if recognized.vehicle else None


def unplugged_choices(config: SunflowConfig, connected: dict[str, bool]) -> list[str]:
    """Wallboxes whose manual vehicle choice ends because the car was unplugged."""
    return [wallbox_id for wallbox_id in config.selected_vehicles if connected.get(wallbox_id) is False]
