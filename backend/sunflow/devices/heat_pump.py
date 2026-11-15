"""Readings of a heat pump: electrical power, heat output and COP from what is measured.

Any measurement is optional; from two of the three values the third is computed.
"""

from dataclasses import dataclass
from typing import Literal

Source = Literal["measured", "computed"]

PHASES = ("l1", "l2", "l3")
DEFAULT_VOLTAGE = 230.0
# Below this the compressor is off or idling: a COP from such readings means nothing
MIN_RUNNING_W = 100.0
PLAUSIBLE_COP = (1.0, 7.0)
# Measured and computed COP may differ this much (sensors average differently)
COP_TOLERANCE = 0.25


@dataclass(frozen=True, kw_only=True)
class HeatPumpReading:
    """What the heat pump draws and delivers now, each with where it comes from."""

    electric_w: float | None = None
    heat_w: float | None = None
    cop: float | None = None
    electric_source: Source | None = None
    heat_source: Source | None = None
    cop_source: Source | None = None
    # Per phase in W, from power or current sensors
    phases_w: dict[str, float] | None = None
    # Why the readings look wrong, e.g. a heat sensor chosen as electrical power
    problem: str | None = None

    def as_dict(self) -> dict[str, object]:
        """For the API."""
        return {
            "electric_w": self.electric_w,
            "heat_w": self.heat_w,
            "cop": self.cop,
            "electric_source": self.electric_source,
            "heat_source": self.heat_source,
            "cop_source": self.cop_source,
            "phases_w": self.phases_w,
            "problem": self.problem,
        }


def _phases(values: dict[str, float | None], voltage: float) -> dict[str, float] | None:
    """Power per phase from power sensors, else from current sensors times the voltage."""
    powers = {phase: values.get(f"power_{phase}") for phase in PHASES}
    if any(v is not None for v in powers.values()):
        return {phase: v for phase, v in powers.items() if v is not None}
    currents = {phase: values.get(f"current_{phase}") for phase in PHASES}
    if any(v is not None for v in currents.values()):
        return {phase: v * voltage for phase, v in currents.items() if v is not None}
    return None


def reading(values: dict[str, float | None], voltage: float = DEFAULT_VOLTAGE) -> HeatPumpReading:
    """Combine the measured values (role -> value in W, A or plain COP) into one reading."""
    phases = _phases(values, voltage)
    electric, electric_source = values.get("power"), "measured"
    if electric is None and phases:
        electric = sum(phases.values())
    heat, heat_source = values.get("heat_power"), "measured"
    measured_cop = values.get("cop")
    cop, cop_source = measured_cop, "measured"
    if electric is None and heat is not None and measured_cop:
        electric, electric_source = heat / measured_cop, "computed"
    if heat is None and electric is not None and measured_cop is not None:
        heat, heat_source = electric * measured_cop, "computed"
    computed_cop = (
        heat / electric
        if heat is not None and electric is not None and electric >= MIN_RUNNING_W and heat_source == "measured"
        else None
    )
    if cop is None and computed_cop is not None:
        cop, cop_source = computed_cop, "computed"
    problem = None
    if computed_cop is not None and not PLAUSIBLE_COP[0] <= computed_cop <= PLAUSIBLE_COP[1]:
        problem = "cop_implausible"
    elif (
        computed_cop is not None
        and measured_cop is not None
        and abs(computed_cop - measured_cop) > COP_TOLERANCE * measured_cop
    ):
        problem = "cop_mismatch"
    return HeatPumpReading(
        electric_w=electric,
        heat_w=heat,
        cop=cop,
        electric_source=electric_source if electric is not None else None,
        heat_source=heat_source if heat is not None else None,
        cop_source=cop_source if cop is not None else None,
        phases_w=phases,
        problem=problem,
    )
