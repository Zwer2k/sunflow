"""Tests for the profile library."""

from pathlib import Path
from typing import Any

from sunflow.devices.profiles import ProfileLibrary, load_profiles


def test_repository_profiles_are_valid(library: ProfileLibrary) -> None:
    """All shipped profiles pass the schema."""
    assert library.errors == []
    assert set(library.profiles) == {
        "solax_modbus/sofar", "huawei_solar/pv_only", "evcc_intg/loadpoint", "generic/switch", "tibber_local/pulse",
        "goecharger_mqtt/charger", "generic/stepped", "generic/current", "solax_modbus/solax_gen4_5",
        "huawei_solar/sun2000_luna2000", "modbus/victron_gx",
        "solaredge_modbus/storedge",
    }
    assert library.integrations == {
        "solax_modbus", "huawei_solar", "evcc_intg", "manual", "tibber_local", "goecharger_mqtt", "solaredge_modbus",
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
