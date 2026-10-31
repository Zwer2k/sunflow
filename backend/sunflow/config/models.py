"""The configuration: devices, tariff, forecast planes, planning and control settings."""

from datetime import datetime
from typing import Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Raised with every change that needs a migration step (see store.MIGRATIONS)
CONFIG_VERSION = 3


class RoleOverride(BaseModel):
    """User deviation from a profile role (advanced mode)."""

    entity_id: str | None = None
    unit: str | None = None
    sign: str | None = None


class FeedIn(BaseModel):
    """Feed-in remuneration of a PV plant."""

    model_config = ConfigDict(allow_inf_nan=False)

    model: Literal["eeg_fixed", "solar_peak", "direct"] = "eeg_fixed"
    rate_ct_per_kwh: float | None = None


class MeasuredBy(BaseModel):
    """Another device that also measures this device's output (e.g. as PV ext)."""

    device_id: str
    role: str


class FlexSettings(BaseModel):
    """How a flexible consumer (EV, heater, switchable load) should be planned."""

    model_config = ConfigDict(allow_inf_nan=False)

    strategy: Literal["cost", "pv_only", "fast"] = "cost"
    # Overrides the target from evcc (% for vehicles, °C for heaters)
    target: float | None = None
    # Daily time ("HH:MM") by which the need has to be met
    ready_by: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    uses_battery: bool = True
    # Price of the alternative heat source (district heating, gas) in ct per kWh of heat
    alternative_cost_ct: float | None = None


class Vehicle(BaseModel):
    """A car as it is: battery and the sensors of its own integration.

    Where it charges, with which card and to what target is a charging profile.
    """

    model_config = ConfigDict(allow_inf_nan=False)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: str = Field(min_length=1)
    capacity_wh: float = Field(gt=0)
    charge_efficiency_pct: float = Field(default=90, ge=50, le=100)
    # State of charge in % from the car's integration (e.g. MQTT)
    soc_entity: str | None = None
    # "on"/"home"/"connected" while the car is plugged in or at home (optional)
    plugged_entity: str | None = None


class ChargingProfile(BaseModel):
    """A vehicle allowed at a wallbox: its card there and how it is charged there."""

    model_config = ConfigDict(allow_inf_nan=False)

    vehicle_id: str
    wallbox_id: str
    # RFID card number at this charger (1..10 at the go-e)
    card: int | None = Field(default=None, ge=1, le=10)
    # Target (%), ready by, strategy, battery use
    flex: FlexSettings = Field(default_factory=FlexSettings)


class VehicleDetection(BaseModel):
    """How a wallbox recognizes the connected vehicle; tried in this order."""

    model_config = ConfigDict(allow_inf_nan=False)

    card: bool = True
    manual: bool = True
    sensor: bool = True
    single: bool = True
    # A card is required at the charger and the car was recognized surely without one
    # (sensor of the car, choice in the app): Sunflow may start the transaction
    authorize: bool = True


class Connection(BaseModel):
    """Address of a device Sunflow talks to itself (profile with a connection)."""

    model_config = ConfigDict(allow_inf_nan=False)

    host: str
    # None: the profile's default port
    port: int | None = Field(default=None, ge=1, le=65535)
    # None: the profile's default unit id
    unit_id: int | None = Field(default=None, ge=0, le=255)


class DeviceConfig(BaseModel):
    """A device managed by Sunflow: what it is and how its entities are read.

    How it is planned, which other devices it belongs to and what its energy earns
    are kept in SunflowConfig, by device id.
    """

    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    ha_device_id: str
    profile: str
    profile_version: int
    name: str
    parameters: dict[str, float] = Field(default_factory=dict)
    role_overrides: dict[str, RoleOverride] = Field(default_factory=dict)
    test_passed_at: datetime | None = None
    # Loadpoints: a car or a heater; None: from the unit of its "SoC" (°C means heater).
    # Own consumers: "heater" when they heat a tank
    kind: Literal["vehicle", "heater"] | None = None
    # Consumers with fixed power steps: option of the select -> watts (0 for off)
    power_steps: dict[str, float] | None = None
    # Devices without an integration: ha_device_id is a pseudo id (direct_<id>)
    connection: Connection | None = None


class TariffConfig(BaseModel):
    """Where the import price comes from: an entity, else a fixed price."""

    model_config = ConfigDict(allow_inf_nan=False)

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
    # Measured PV strings of this plane ("<device id>:<string>"), for learning per plane
    strings: list[str] = Field(default_factory=list)
    # Learning ignores older data, e.g. from before the modules were cleaned
    learn_since: datetime | None = None

    @property
    def key(self) -> str:
        """Stable id of the plane in stored data."""
        return f"{self.source}:{self.ref}"

    @property
    def display_name(self) -> str:
        """Return the user's name, else the detected one."""
        return self.custom_name or self.name


class WearCalculation(BaseModel):
    """Battery wear from purchase price and the manufacturer's cycle rating."""

    model_config = ConfigDict(allow_inf_nan=False)

    price_eur: float = Field(gt=0)
    cycles: float = Field(gt=0)
    capacity_kwh: float = Field(gt=0)
    # Depth of discharge the cycle rating refers to
    depth_of_discharge_pct: float = Field(default=90, gt=0, le=100)
    # Remaining capacity at the end of the rated cycles; the capacity fades towards it
    end_of_life_pct: float = Field(default=70, gt=0, le=100)

    def ct_per_kwh(self) -> float:
        """Price per kWh discharged over the battery's rated life."""
        average_capacity = self.capacity_kwh * (1 + self.end_of_life_pct / 100) / 2
        lifetime_kwh = self.cycles * average_capacity * self.depth_of_discharge_pct / 100
        return self.price_eur * 100 / lifetime_kwh


class OptimizerConfig(BaseModel):
    """User settings for planning."""

    model_config = ConfigDict(allow_inf_nan=False)

    wear_ct_per_kwh: float = 3.0
    # When set, wear_ct_per_kwh is derived from it
    wear_calculation: WearCalculation | None = None
    allow_grid_charge: bool = True
    strategy: Literal["balanced", "pv_absorption", "autarky"] = "balanced"
    # Kept in the battery in every strategy; only an outage (EPS) may use it
    backup_reserve: bool = False
    backup_reserve_pct: float = Field(default=30.0, ge=0, le=100)
    # Load forecast: average daily household consumption and the threshold above
    # which hours count as large sporadic loads (e.g. EV charging) and are left out
    daily_consumption_kwh: float | None = None
    large_load_threshold_w: float | None = 3500.0
    # Battery care: everyday charge limit and how often a full charge (balancing) is wanted
    max_soc_pct: float = Field(default=100, ge=50, le=100)
    # "always": the limit holds every day; "unless_poor_days": lifted while a day with less
    # PV than consumption is forecast within poor_days_ahead days, to store what PV gives
    max_soc_mode: Literal["always", "unless_poor_days"] = "always"
    poor_days_ahead: int = Field(default=2, ge=1, le=5)
    full_charge_every_days: int | None = Field(default=None, ge=1, le=90)
    # When PV did not fill the battery for this long, charge it full from the grid at the cheapest time
    full_charge_grid_after_days: int | None = Field(default=None, ge=1, le=120)
    # Adapt the PV forecast to this site from shadow data; weather entity for the cloud
    # coverage (None: the first weather entity found)
    pv_learning: bool = True
    # How much to trust the PV forecast: expected value, or partly the pessimistic one
    pv_risk: Literal["expected", "balanced", "cautious"] = "balanced"
    # Grid friendly: keep export and import below these powers where it costs little
    grid_friendly: bool = False
    export_limit_w: float | None = Field(default=None, gt=0)
    import_limit_w: float | None = Field(default=None, gt=0)
    weather_entity: str | None = None

    @model_validator(mode="after")
    def _derive_wear(self) -> "OptimizerConfig":
        if self.wear_calculation is not None:
            self.wear_ct_per_kwh = round(self.wear_calculation.ct_per_kwh(), 2)
        return self


Strategy = Literal["balanced", "pv_absorption", "autarky"]
# Position of the home battery in the priority list
BATTERY_PRIORITY_ID = "battery"


class DeviceOverride(BaseModel):
    """Temporary change of a consumer's planning from the control tab."""

    model_config = ConfigDict(allow_inf_nan=False)

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

    model_config = ConfigDict(allow_inf_nan=False)

    # BATTERY_PRIORITY_ID or the id of a vehicle or hot water loadpoint
    device_id: str
    # % for battery and vehicles, °C for hot water tanks (name kept for stored configs)
    min_pct: float = Field(ge=0, le=100)


class StrategyOverride(BaseModel):
    """Temporary battery strategy from the control tab."""

    model_config = ConfigDict(allow_inf_nan=False)

    strategy: Strategy
    until: datetime | None = None

    def active(self, now: datetime) -> bool:
        """Return True while the override applies."""
        return self.until is None or self.until > now


ForecastKind = Literal["total", "plant", "plane"]


class IntegrationSettings(BaseModel):
    """What the Sunflow integration in Home Assistant gets."""

    # PV forecasts for the energy dashboard: in total, per plant (inverter), per plane (string)
    forecast_kinds: list[ForecastKind] = Field(default_factory=lambda: ["total", "plant", "plane"])


class OptimizerPlacement(BaseModel):
    """Where one optimizer (Home Assistant device) hangs and sits; unset fields are proposed."""

    ha_device_id: str
    # "<device id>:<string>"; "" for none, None for the proposal
    string: str | None = None
    # Position on the roof, row 0 at the top
    row: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    name: str | None = None
    # Landscape instead of portrait
    rotated: bool | None = None


class OptimizerSettings(BaseModel):
    """Optimizers on the PV strings."""

    # Plant (device id) the Tigo optimizers hang on; None: proposed
    tigo_plant: str | None = None
    placements: list[OptimizerPlacement] = Field(default_factory=list)


class DisplaySettings(BaseModel):
    """What the App's pages show; phones and wider screens apart."""

    weather_mobile: bool = True
    weather_desktop: bool = True


class ControlSettings(BaseModel):
    """Whether Sunflow carries out its plan on the devices (off: planning only)."""

    enabled: bool = False
    # Per device id; a device is controlled only when both switches are on
    devices: dict[str, bool] = Field(default_factory=dict)


class SunflowConfig(BaseModel):
    """The complete configuration."""

    # Fields of a newer version survive a downgrade instead of being dropped on save
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)

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
    # Recommendations hidden by the user, as "<id>#<fingerprint>"
    dismissed_recommendations: list[str] = Field(default_factory=list)
    # Measured quantity ("grid", "load") -> device id; missing means automatic
    measurement_sources: dict[str, str] = Field(default_factory=dict)
    # Per consumer id: all conditions must hold before it gets PV or battery energy
    priority_conditions: dict[str, list[PriorityCondition]] = Field(default_factory=dict)
    control: ControlSettings = Field(default_factory=ControlSettings)
    integration: IntegrationSettings = Field(default_factory=IntegrationSettings)
    display: DisplaySettings = Field(default_factory=DisplaySettings)
    optimizer_settings: OptimizerSettings = Field(default_factory=OptimizerSettings)
    vehicles: list[Vehicle] = Field(default_factory=list)
    # Vehicle at wallbox: card, target, ready by
    charging: list[ChargingProfile] = Field(default_factory=list)
    # By device id: how a consumer is planned (missing: defaults)
    planning: dict[str, FlexSettings] = Field(default_factory=dict)
    # By wallbox id: how the connected vehicle is recognized
    vehicle_detection: dict[str, VehicleDetection] = Field(default_factory=dict)
    # By inverter id: HA devices of its battery modules (missing: its sub-devices in HA)
    battery_modules: dict[str, list[str]] = Field(default_factory=dict)
    # By plant id: what its feed-in earns
    feed_in: dict[str, FeedIn] = Field(default_factory=dict)
    # By device id: another device whose sensors measure it
    measured_by: dict[str, MeasuredBy] = Field(default_factory=dict)
    # Switched off in the setup (e.g. a wallbox both as evcc loadpoint and directly): kept
    # with all settings, but planning, measuring and control only see self.devices
    disabled_devices: list[DeviceConfig] = Field(default_factory=list)
    # Wallbox id -> vehicle chosen in the control tab (until the car is unplugged)
    selected_vehicles: dict[str, str] = Field(default_factory=dict)

    def flex_of(self, device_id: str) -> FlexSettings:
        """How a consumer is planned (defaults when nothing was set)."""
        return self.planning.get(device_id) or FlexSettings()

    def detection_of(self, wallbox_id: str) -> VehicleDetection:
        """How a wallbox recognizes the connected vehicle."""
        return self.vehicle_detection.get(wallbox_id) or VehicleDetection()

    def charging_profile(self, vehicle_id: str, wallbox_id: str) -> ChargingProfile | None:
        """Card and charging settings of a vehicle at a wallbox, None if it may not charge there."""
        return next((c for c in self.charging if c.vehicle_id == vehicle_id and c.wallbox_id == wallbox_id), None)

    def forget_device(self, device_id: str) -> None:
        """Drop everything that refers to a removed device."""
        for relation in (self.planning, self.vehicle_detection, self.battery_modules, self.feed_in, self.measured_by):
            relation.pop(device_id, None)
        self.measured_by = {k: v for k, v in self.measured_by.items() if v.device_id != device_id}
        self.charging = [c for c in self.charging if c.wallbox_id != device_id]

    def vehicles_at(self, wallbox_id: str) -> list[Vehicle]:
        """Vehicles that may charge at a wallbox."""
        allowed = {c.vehicle_id for c in self.charging if c.wallbox_id == wallbox_id}
        return [v for v in self.vehicles if v.id in allowed]

    def vehicle(self, vehicle_id: str) -> Vehicle | None:
        """Return a vehicle by its id."""
        return next((v for v in self.vehicles if v.id == vehicle_id), None)

    def device(self, device_id: str) -> DeviceConfig | None:
        """Return a configured device by its Sunflow id."""
        return next((d for d in self.devices if d.id == device_id), None)

    def effective_flex(
        self, device: DeviceConfig, now: datetime, base: FlexSettings | None = None
    ) -> tuple[FlexSettings, bool]:
        """Planning settings (base: e.g. a charging profile) with an active override, and whether it is paused."""
        settings = base or self.flex_of(device.id)
        override = self.overrides.get(device.id)
        if override is None or not override.active(now):
            return settings, False
        changes = {k: v for k, v in (("strategy", override.strategy), ("target", override.target)) if v is not None}
        return settings.model_copy(update=changes), override.paused

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

    def ranked_above(self, device_id: str) -> set[str]:
        """Ids of the entries with a higher priority than the given one."""
        order = self.priority_order()
        return set(order[: order.index(device_id)]) if device_id in order else set()

    def valid_conditions(self, device_id: str) -> list[PriorityCondition]:
        """Conditions that apply: those referring to entries ranked above the consumer.

        The others stay stored (hidden) and apply again once their entry moves up.
        """
        above = self.ranked_above(device_id)
        return [c for c in self.priority_conditions.get(device_id, []) if c.device_id in above]

    def hidden_conditions(self, device_id: str) -> list[PriorityCondition]:
        """Stored conditions that currently do not apply because their entry ranks lower."""
        above = self.ranked_above(device_id)
        return [c for c in self.priority_conditions.get(device_id, []) if c.device_id not in above]
