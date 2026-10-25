"""Persistent Sunflow configuration."""

from datetime import datetime
import json
from pathlib import Path
from typing import Literal
import uuid

from pydantic import BaseModel, Field

CONFIG_VERSION = 1


class RoleOverride(BaseModel):
    """User deviation from a profile role (advanced mode)."""

    entity_id: str | None = None
    unit: str | None = None
    sign: str | None = None


class FeedIn(BaseModel):
    """Feed-in remuneration of a PV plant."""

    model: Literal["eeg_fixed", "solar_peak", "direct"] = "eeg_fixed"
    rate_ct_per_kwh: float | None = None


class MeasuredBy(BaseModel):
    """Another device that also measures this device's output (e.g. as PV ext)."""

    device_id: str
    role: str


class FlexSettings(BaseModel):
    """How a flexible consumer (EV, heater, switchable load) should be planned."""

    strategy: Literal["cost", "pv_only", "fast"] = "cost"
    # Overrides the target from evcc (% for vehicles, °C for heaters)
    target: float | None = None
    # Daily time ("HH:MM") by which the need has to be met
    ready_by: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    uses_battery: bool = True
    # Price of the alternative heat source (district heating, gas) in ct per kWh of heat
    alternative_cost_ct: float | None = None


class DeviceConfig(BaseModel):
    """A device managed by Sunflow."""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ha_device_id: str
    profile: str
    profile_version: int
    name: str
    parameters: dict[str, float] = Field(default_factory=dict)
    role_overrides: dict[str, RoleOverride] = Field(default_factory=dict)
    feed_in: FeedIn | None = None
    measured_by: MeasuredBy | None = None
    test_passed_at: datetime | None = None
    flex: FlexSettings = Field(default_factory=FlexSettings)


class TariffConfig(BaseModel):
    """Where the import price comes from: an entity, else a fixed price."""

    import_entity_id: str | None = None
    fixed_price_ct: float | None = None


class ForecastPlane(BaseModel):
    """A PV forecast plane assigned to a plant (Sunflow device id)."""

    source: str
    ref: str
    name: str
    device_id: str | None = None
    # Set by the user; the detected name may be generic ("Solar production forecast")
    custom_name: str | None = None

    @property
    def display_name(self) -> str:
        """Return the user's name, else the detected one."""
        return self.custom_name or self.name


class OptimizerConfig(BaseModel):
    """User settings for planning."""

    wear_ct_per_kwh: float = 3.0
    allow_grid_charge: bool = True
    strategy: Literal["balanced", "pv_absorption", "backup"] = "balanced"
    backup_reserve_pct: float = 30.0
    # Load forecast: average daily household consumption and the threshold above
    # which hours count as large sporadic loads (e.g. EV charging) and are left out
    daily_consumption_kwh: float | None = None
    large_load_threshold_w: float | None = 3500.0


Strategy = Literal["balanced", "pv_absorption", "backup"]
# Position of the home battery in the priority list
BATTERY_PRIORITY_ID = "battery"


class DeviceOverride(BaseModel):
    """Temporary change of a consumer's planning from the control tab."""

    strategy: Literal["cost", "pv_only", "fast"] | None = None
    target: float | None = None
    paused: bool = False
    # None: until reset
    until: datetime | None = None

    def active(self, now: datetime) -> bool:
        """Return True while the override applies."""
        return self.until is None or self.until > now


class PriorityCondition(BaseModel):
    """A consumer only gets PV or battery energy once another storage reached this level."""

    # BATTERY_PRIORITY_ID or the id of a vehicle loadpoint
    device_id: str
    min_pct: float = Field(ge=0, le=100)


class StrategyOverride(BaseModel):
    """Temporary battery strategy from the control tab."""

    strategy: Strategy
    until: datetime | None = None

    def active(self, now: datetime) -> bool:
        """Return True while the override applies."""
        return self.until is None or self.until > now


class SunflowConfig(BaseModel):
    """The complete configuration."""

    version: int = CONFIG_VERSION
    devices: list[DeviceConfig] = Field(default_factory=list)
    tariff: TariffConfig = Field(default_factory=TariffConfig)
    forecast_planes: list[ForecastPlane] = Field(default_factory=list)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    setup_completed: bool = False
    overrides: dict[str, DeviceOverride] = Field(default_factory=dict)
    strategy_override: StrategyOverride | None = None
    # Device ids and BATTERY_PRIORITY_ID, highest priority first
    priorities: list[str] = Field(default_factory=list)
    # Per consumer id: all conditions must hold before it gets PV or battery energy
    priority_conditions: dict[str, list[PriorityCondition]] = Field(default_factory=dict)

    def device(self, device_id: str) -> DeviceConfig | None:
        """Return a configured device by its Sunflow id."""
        return next((d for d in self.devices if d.id == device_id), None)

    def effective_flex(self, device: DeviceConfig, now: datetime) -> tuple[FlexSettings, bool]:
        """Return the planning settings with an active override applied, and whether it is paused."""
        override = self.overrides.get(device.id)
        if override is None or not override.active(now):
            return device.flex, False
        changes = {k: v for k, v in (("strategy", override.strategy), ("target", override.target)) if v is not None}
        return device.flex.model_copy(update=changes), override.paused

    def effective_strategy(self, now: datetime) -> Strategy:
        """Return the battery strategy with an active override applied."""
        if self.strategy_override is not None and self.strategy_override.active(now):
            return self.strategy_override.strategy
        return self.optimizer.strategy

    def priority_order(self) -> list[str]:
        """Return all flexible device ids and the battery in priority order (unlisted ones last)."""
        known = [d.id for d in self.devices] + [BATTERY_PRIORITY_ID]
        order = [i for i in self.priorities if i in known]
        return order + [i for i in known if i not in order]


class ConfigStore:
    """Load and save the configuration as JSON in the data directory."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize the store."""
        self._path = data_dir / "config.json"

    def load(self) -> SunflowConfig:
        """Return the stored configuration or an empty one."""
        if not self._path.exists():
            return SunflowConfig()
        return SunflowConfig.model_validate_json(self._path.read_text())

    def save(self, config: SunflowConfig) -> None:
        """Write the configuration atomically."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps(config.model_dump(mode="json"), indent=2))
        tmp.replace(self._path)
