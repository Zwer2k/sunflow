"""Tests for the setup summary."""

from sunflow.analysis.summary import STEP_DEVICES, STEP_SOURCES, build_summary
from sunflow.config.models import (
    DeviceConfig,
    FeedIn,
    ForecastPlane,
    MeasuredBy,
    SunflowConfig,
    TariffConfig,
)
from sunflow.devices.profiles import ProfileLibrary


def _complete_config() -> SunflowConfig:
    sofar = DeviceConfig(
        id="sofar",
        ha_device_id="d_inv",
        profile="solax_modbus/sofar",
        profile_version=1,
        name="Sofar",
        parameters={"max_charge": 6000, "max_discharge": 6000},
    )
    huawei = DeviceConfig(
        id="huawei",
        ha_device_id="d_hw",
        profile="huawei_solar/pv_only",
        profile_version=1,
        name="Huawei",
    )
    return SunflowConfig(
        devices=[sofar, huawei],
        feed_in={
            "sofar": FeedIn(model="eeg_fixed", rate_ct_per_kwh=8.2),
            "huawei": FeedIn(model="eeg_fixed", rate_ct_per_kwh=7.9),
        },
        measured_by={"huawei": MeasuredBy(device_id="sofar", role="pv_ext_power")},
        tariff=TariffConfig(import_entity_id="sensor.price"),
        forecast_planes=[
            ForecastPlane(source="solcast", ref="a", name="Nord-Ost", device_id="sofar"),
            ForecastPlane(source="solcast", ref="b", name="Süd-Ost", device_id="huawei"),
        ],
    )


def test_complete_setup(library: ProfileLibrary) -> None:
    """A complete setup only reports the pending device test."""
    summary = build_summary(_complete_config(), library.profiles)

    assert summary.can_complete
    assert [i.level for i in summary.issues] == ["info"]
    assert summary.devices[1]["measured_by"] == "Sofar"
    assert summary.forecast_planes == [{"name": "Nord-Ost", "plant": "Sofar"}, {"name": "Süd-Ost", "plant": "Huawei"}]


def test_empty_setup(library: ProfileLibrary) -> None:
    """Only a missing device blocks completion; a missing price is a warning."""
    summary = build_summary(SunflowConfig(), library.profiles)

    assert not summary.can_complete
    assert {(i.level, i.step) for i in summary.issues} == {("error", STEP_DEVICES), ("warning", STEP_SOURCES)}


def test_minimal_setup_can_complete(library: ProfileLibrary) -> None:
    """A device with a fixed price and no forecast can still be completed."""
    config = _complete_config()
    config.tariff = TariffConfig(fixed_price_ct=30)
    config.forecast_planes = []

    summary = build_summary(config, library.profiles)

    assert summary.can_complete
    assert not any(i.level == "error" for i in summary.issues)
    assert any("PV-Prognose" in i.message for i in summary.issues if i.level == "warning")


def test_open_items_are_warnings(library: ProfileLibrary) -> None:
    """Defaults, missing feed-in, link and plane assignment are warnings."""
    config = _complete_config()
    config.devices[0].parameters = {}
    config.feed_in.pop("huawei")
    config.measured_by.pop("huawei")
    config.forecast_planes[1].device_id = None

    summary = build_summary(config, library.profiles)

    assert summary.can_complete
    warnings = [i.message for i in summary.issues if i.level == "warning"]
    assert len(warnings) == 4
