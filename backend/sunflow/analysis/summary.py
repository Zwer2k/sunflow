"""Setup summary: what is configured and what is still missing."""

from dataclasses import dataclass, field
from typing import Any, Literal

from sunflow.config.models import SunflowConfig
from sunflow.i18n import t

# Wizard step indices the UI links to
# Settings areas the UI opens for an issue
STEP_DEVICES = "devices"
STEP_PARAMETERS = "devices"
STEP_DEVICE_TEST = "test"
STEP_SITE = "site"
STEP_SOURCES = "sources"

# Defaults that come from register ranges, not from the device data sheet
DEFAULTS_TO_CONFIRM = ("max_charge", "max_discharge")


@dataclass(frozen=True, kw_only=True)
class Issue:
    """Something to fix (error) or check (warning, info) before planning."""

    level: Literal["error", "warning", "info"]
    step: str
    message: str


@dataclass(kw_only=True)
class SetupSummary:
    """Overview for the last wizard step."""

    devices: list[dict[str, Any]] = field(default_factory=list)
    tariff: str | None = None
    fixed_price_ct: float | None = None
    forecast_planes: list[dict[str, Any]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    completed: bool = False

    @property
    def can_complete(self) -> bool:
        """Return True if no error blocks finishing the setup."""
        return not any(i.level == "error" for i in self.issues)


def build_summary(
    config: SunflowConfig, profiles: dict[str, dict[str, Any]]
) -> SetupSummary:
    """Collect configured items and open issues."""
    summary = SetupSummary(
        tariff=config.tariff.import_entity_id,
        fixed_price_ct=config.tariff.fixed_price_ct,
        completed=config.setup_completed,
    )
    names = {d.id: d.name for d in config.devices}
    if not config.devices:
        summary.issues.append(
            Issue(level="error", step=STEP_DEVICES, message=t("Es ist noch kein Gerät übernommen."))
        )

    pv_plants = []
    for device in config.devices:
        profile = profiles.get(device.profile)
        if profile is None:
            summary.issues.append(
                Issue(level="error", step=STEP_DEVICES, message=t("{name}: Profil {profile} fehlt.", name=device.name, profile=device.profile))
            )
            continue
        capabilities = profile["capabilities"]
        summary.devices.append(
            {
                "id": device.id,
                "name": device.name,
                "profile": device.profile,
                "capabilities": capabilities,
                "parameters": device.parameters,
                "feed_in": feed_in.model_dump() if (feed_in := config.feed_in.get(device.id)) else None,
                "measured_by": names.get(link.device_id) if (link := config.measured_by.get(device.id)) else None,
                "overrides": sorted(device.role_overrides),
            }
        )
        params = profile.get("parameters", {})
        unconfirmed = [p for p in DEFAULTS_TO_CONFIRM if p in params and p not in device.parameters]
        if unconfirmed:
            summary.issues.append(
                Issue(
                    level="warning",
                    step=STEP_PARAMETERS,
                    message=t("{name}: Lade-/Entladeleistung ist nur eine Vorgabe – bitte mit dem Datenblatt abgleichen.", name=device.name),
                )
            )
        if "pv" in capabilities:
            pv_plants.append(device)
            if (feed_in := config.feed_in.get(device.id)) is None or feed_in.rate_ct_per_kwh is None:
                summary.issues.append(
                    Issue(level="warning", step=STEP_SOURCES, message=t("{name}: Einspeisevergütung fehlt.", name=device.name))
                )
        if {"battery_window", "battery_control"} & set(capabilities) and device.test_passed_at is None:
            summary.issues.append(
                Issue(
                    level="info",
                    step=STEP_DEVICE_TEST,
                    message=t("{name}: Gerätetest steht noch aus – Steuerung ist noch nicht bestätigt.", name=device.name),
                )
            )

    measuring = [
        d for d in config.devices if "pv_ext" in profiles.get(d.profile, {}).get("capabilities", [])
    ]
    for device in pv_plants:
        capabilities = profiles[device.profile]["capabilities"]
        if measuring and "battery" not in capabilities and device.id not in config.measured_by:
            summary.issues.append(
                Issue(
                    level="warning",
                    step=STEP_SITE,
                    message=t("{name}: Nicht festgelegt, ob ein anderes Gerät diese Anlage als PV extern misst.", name=device.name),
                )
            )

    if config.tariff.import_entity_id is None and config.tariff.fixed_price_ct is None:
        summary.issues.append(
            Issue(
                level="warning",
                step=STEP_SOURCES,
                message=t("Kein Bezugspreis angegeben – Sunflow optimiert dann nur auf Eigenverbrauch."),
            )
        )

    assigned = {p.device_id for p in config.forecast_planes if p.device_id}
    summary.forecast_planes = [
        # Unassigned planes are fine: the same roof may exist at several providers
        {"name": p.display_name, "plant": names.get(p.device_id) if p.device_id else None}
        for p in config.forecast_planes
    ]
    if pv_plants and not assigned:
        summary.issues.append(
            Issue(
                level="warning",
                step=STEP_SOURCES,
                message=t("Keine PV-Prognose zugeordnet – die Planung kann PV-Erträge dann nicht vorhersehen."),
            )
        )
    else:
        for device in pv_plants:
            if device.id not in assigned:
                summary.issues.append(
                    Issue(level="warning", step=STEP_SOURCES, message=t("{name}: Keine Prognosefläche zugeordnet.", name=device.name))
                )
    return summary
