"""Tests for the profile library."""

from pathlib import Path
from typing import Any

import pytest
from sunflow.devices.discovery import Snapshot, match_profile
from sunflow.devices.profiles import ProfileLibrary, load_profiles
from sunflow.devices.resolver import preview


def test_repository_profiles_are_valid(library: ProfileLibrary) -> None:
    """All shipped profiles pass the schema."""
    assert library.errors == []
    assert set(library.profiles) == {
        "solax_modbus/sofar", "huawei_solar/pv_only", "evcc_intg/loadpoint", "generic/switch", "generic/meter", "tibber_local/pulse",
        "edl21/meter", "homewizard/p1", "powerfox/poweropti", "discovergy/meter", "goecharger_mqtt/charger", "goecharger/charger",
        "generic/stepped", "generic/current", "solax_modbus/solax_gen4_5", "huawei_solar/sun2000_luna2000", "modbus/victron_gx",
        "solaredge_modbus/storedge", "fronius/gen24", "goodwe/hybrid", "kostal_plenticore/plenticore", "indevolt/indevolt",
        "homevolt/homevolt", "marstek_modbus/venus", "zendure_ha/manager", "generic/heat_pump", "generic/relays",
        "generic/grid_meter", "solax_modbus/growatt_hybrid", "solax_modbus/growatt_pv", "solarman/deye_micro",
    }
    assert library.integrations == {
        "solax_modbus", "huawei_solar", "evcc_intg", "manual", "tibber_local", "edl21", "homewizard", "powerfox", "discovergy",
        "goecharger_mqtt", "goecharger", "solaredge_modbus", "fronius", "goodwe", "kostal_plenticore", "indevolt", "homevolt",
        "marstek_modbus", "zendure_ha", "solarman",
    }


def test_control_sections_match_capabilities(library: ProfileLibrary) -> None:
    """Battery control by actions and PV limiting are declared as capabilities (the UI goes by them)."""
    for profile in library.profiles.values():
        capabilities = set(profile["capabilities"])
        assert ("battery_control" in profile) == ("battery_control" in capabilities), profile["profile"]
        assert ("pv_limit" in profile) <= ("curtail" in capabilities), profile["profile"]


def test_invalid_profile_is_reported(tmp_path: Path, schema: dict[str, Any]) -> None:
    """A profile violating the schema is collected as error, not loaded."""
    (tmp_path / "bad.yaml").write_text(
        "profile: Bad-Id\nversion: 1\nmatch: {integration: x}\n"
        "capabilities: [teleport]\nroles: {soc: {key: soc}}\n"
    )
    (tmp_path / "broken.yaml").write_text("profile: [unclosed\n")

    library = load_profiles(tmp_path, schema)

    assert library.profiles == {}
    messages = {e.path.name: e.message for e in library.errors}
    assert "profile" in messages["bad.yaml"]
    assert "capabilities/0" in messages["bad.yaml"]
    assert "broken.yaml" in messages


def test_duplicate_profile_id(tmp_path: Path, schema: dict[str, Any]) -> None:
    """The second file with the same profile id is rejected."""
    content = (
        "profile: x/y\nversion: 1\nmatch: {integration: x}\n"
        "capabilities: [pv]\nroles: {pv_power: {key: pv}}\n"
    )
    (tmp_path / "a.yaml").write_text(content)
    (tmp_path / "b.yaml").write_text(content)

    library = load_profiles(tmp_path, schema)

    assert list(library.profiles) == ["x/y"]
    assert library.errors[0].message == "Duplicate profile x/y"


def test_every_profile_is_documented(library: ProfileLibrary) -> None:
    """The device list in the docs names every shipped profile."""
    text = (Path(__file__).resolve().parents[1] / "docs" / "geraete.md").read_text()
    assert [p for p in library.profiles if f"`{p}`" not in text] == []


# unique_id, state, unit per entity of a meter, as the integrations name them
EDL21 = [
    ("1ESY1161234567_1-0:16.7.0*255", "-1250", "W"),
    ("1ESY1161234567_1-0:1.8.0*255", "12345678.9", "Wh"),
    ("1ESY1161234567_1-0:2.8.0*255", "2500.5", "kWh"),
    # Same digits around another middle: the star in the key is no wildcard
    ("1ESY1161234567_1-0:16.7.0x255", "999", "W"),
]
HOMEWIZARD = [
    ("HWE-P1_5c2fafabcdef_active_power_w", "-1250", "W"),
    ("HWE-P1_5c2fafabcdef_active_power_l1_w", "-400", "W"),
    ("HWE-P1_5c2fafabcdef_total_power_import_kwh", "12345.6789", "kWh"),
    ("HWE-P1_5c2fafabcdef_total_power_export_kwh", "2500.5", "kWh"),
]
POWERFOX = [
    ("9x9x1f12xx3x_power", "-1250", "W"),
    ("9x9x1f12xx3x_energy_usage", "12345.6789", "kWh"),
    ("9x9x1f12xx3x_energy_return", "2500.5", "kWh"),
]
DISCOVERGY = [
    ("abc123-power", "-1250", "W"),
    ("abc123-power1", "-400", "W"),
    ("abc123-energy", "12345.6789", "kWh"),
    ("abc123-energyOut", "2500.5", "kWh"),
]


@pytest.mark.parametrize(
    ("domain", "rows", "profile_id"),
    [
        pytest.param("edl21", EDL21, "edl21/meter", id="edl21"),
        pytest.param("homewizard", HOMEWIZARD, "homewizard/p1", id="homewizard"),
        pytest.param("powerfox", POWERFOX, "powerfox/poweropti", id="powerfox"),
        pytest.param("discovergy", DISCOVERGY, "discovergy/meter", id="discovergy"),
    ],
)
def test_grid_meters(library: ProfileLibrary, domain: str, rows: list[tuple[str, str, str]], profile_id: str) -> None:
    """A meter is matched by its unique_ids and measures the grid: + import, - export, readings in Wh."""
    entities = [{"entity_id": f"sensor.m{i}", "unique_id": uid, "device_id": "meter"} for i, (uid, _, _) in enumerate(rows)]
    states = {f"sensor.m{i}": {"state": state, "attributes": {"unit_of_measurement": unit}} for i, (_, state, unit) in enumerate(rows)}
    snapshot = Snapshot(entries=[], devices=[{"id": "meter"}], entities=entities, states=states, energy_prefs=None)

    matched = match_profile(library, domain, {"id": "meter"}, entities, entry_has_battery=False)
    values = {r.role: r.value for r in preview(library.profiles[profile_id], snapshot, "meter")}

    assert matched == profile_id
    assert values == pytest.approx({"grid_power": -1250.0, "grid_import_energy": 12345678.9, "grid_export_energy": 2500500.0})


def test_homewizard_kwh_meter_is_no_grid_meter(library: ProfileLibrary) -> None:
    """HomeWizard kWh meters share the P1 meter's keys but measure a single circuit."""
    entities = [{"entity_id": "sensor.m", "unique_id": "HWE-KWH3_5c2fafabcdef_active_power_w", "device_id": "meter"}]

    assert match_profile(library, "homewizard", {"id": "meter"}, entities, entry_has_battery=False) is None
