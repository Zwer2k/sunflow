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


class SunflowConfig(BaseModel):
    """The complete configuration."""

    version: int = CONFIG_VERSION
    devices: list[DeviceConfig] = Field(default_factory=list)
    tariff: TariffConfig = Field(default_factory=TariffConfig)
    forecast_planes: list[ForecastPlane] = Field(default_factory=list)
    optimizer: OptimizerConfig = Field(default_factory=OptimizerConfig)
    setup_completed: bool = False

    def device(self, device_id: str) -> DeviceConfig | None:
        """Return a configured device by its Sunflow id."""
        return next((d for d in self.devices if d.id == device_id), None)


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
