"""States of the HPSU and valid heating (design/heat_pump.md, section 4)."""

from datetime import UTC, datetime, timedelta
from typing import Any

from heat_pump_node import joined
import pytest
from sunflow.devices.profiles import ProfileLibrary
from sunflow.heat_pump.states import HeatingTracker, Reading, StateSettings, read

PROFILE = "esphome/rotex_hpsu_can"
NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
MIN = timedelta(minutes=1)


@pytest.fixture
def profile(library: ProfileLibrary) -> dict[str, Any]:
    """The HPSU profile."""
    return library.profiles[PROFILE]


def _state(raw: str | None, changed: datetime | None = None) -> dict[str, Any] | None:
    if raw is None:
        return None
    return {"state": raw, "attributes": {}, "last_changed": changed.isoformat() if changed else None}


def reading(
    profile: dict[str, Any],
    *,
    program: str = "Heizen",
    activity: str = "Heizen",
    activity_since: datetime | None = NOW - timedelta(hours=1),
    strategy: str = "Fest",
    block: str = "Aus",
    error: str | None = "Kein Fehler",
    bypass: float | None = 0.0,
    bypass_since: datetime | None = NOW - timedelta(hours=1),
    setpoint: float | None = 30.0,
    max_setpoint: float | None = 30.0,
    compressor: str = "on",
) -> Reading:
    """A reading of the HPSU as its German firmware shows it; by default heating validly."""
    states = {
        "program": _state(program),
        "activity": _state(activity, activity_since),
        "strategy_select": _state(strategy),
        "heating_block": _state(block),
        "error": _state(error),
        "compressor": _state(compressor),
        "bypass_valve": _state(None if bypass is None else str(bypass), bypass_since),
    }
    values = {"bypass_valve": bypass, "flow_setpoint": setpoint, "max_flow_setpoint": max_setpoint}
    return read(profile, joined(), states, values)


def tracker(profile: dict[str, Any]) -> HeatingTracker:
    return HeatingTracker(StateSettings.from_profile(profile))


# Mapping of the states


@pytest.mark.parametrize(
    ("shown", "program", "key"),
    [
        ("Bereitschaft", "standby", "standby"),
        ("Heizen", "heating", "heating"),
        ("Absenken", "heating", "lowering"),
        ("Sommer", "summer", "summer"),
        ("Automatik 1", "heating", "automatic_1"),
        ("Automatik 2", "heating", "automatic_2"),
        ("Kühlen", "cooling", "cooling"),
        ("Party", "unknown", None),
    ],
)
def test_programs(profile: dict[str, Any], shown: str, program: str, key: str | None) -> None:
    """Every operating mode of the HPSU maps to its canonical program; an unknown one is noted."""
    result = reading(profile, program=shown)

    assert (result.program, result.program_key) == (program, key)
    assert (result.unknown == {"program": shown}) is (key is None)


@pytest.mark.parametrize(
    ("shown", "activity"),
    [
        ("Bereitschaft", "idle"),
        ("Heizen", "heating"),
        ("Kühlen", "cooling"),
        ("Abtauen", "defrost"),
        ("Warmwasserbereitung", "hot_water"),
        ("Lüften", "unknown"),
    ],
)
def test_activities(profile: dict[str, Any], shown: str, activity: str) -> None:
    """Every mode of operation maps to its canonical activity."""
    assert reading(profile, activity=shown).activity == activity


def test_strategy_and_block(profile: dict[str, Any]) -> None:
    """hk_function gives the strategy; the room thermostat "An" blocks heating."""
    assert reading(profile).strategy == "fixed"
    assert reading(profile, strategy="Witterungsgeführt").strategy == "weather"
    assert reading(profile, block="An").blocked is True
    assert reading(profile).blocked is False


@pytest.mark.parametrize(
    ("error", "fault", "unknown"),
    [
        pytest.param("Kein Fehler", False, False, id="no error"),
        pytest.param("E9001 Rücklauffühler", True, False, id="E code"),
        pytest.param("E75 Fehler Außentemperaturfühler", True, False, id="short E code"),
        pytest.param("W8006 Warnung Druckverlust", False, False, id="warning"),
        pytest.param("E9999 Neuer Fehler", True, False, id="E code unknown to the manifest"),
        pytest.param("Irgendwas", False, True, id="unknown text"),
    ],
)
def test_faults(profile: dict[str, Any], error: str, fault: bool, unknown: bool) -> None:
    """Codes starting with E are faults, warnings are not; an unknown text that is no E code is noted."""
    result = reading(profile, error=error)

    assert result.fault is fault
    assert ("error" in result.unknown) is unknown


# Valid heating


def test_valid_heating(profile: dict[str, Any]) -> None:
    """All conditions at once: valid."""
    assert tracker(profile).evaluate(reading(profile), NOW, usable=True).valid


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        pytest.param({"program": "Sommer"}, "program_summer", id="summer"),
        pytest.param({"program": "Bereitschaft"}, "program_standby", id="standby"),
        pytest.param({"program": "Kühlen"}, "program_cooling", id="cooling"),
        pytest.param({"program": "Party"}, "program_unknown", id="unknown program"),
        pytest.param({"activity": "Warmwasserbereitung"}, "activity_hot_water", id="hot water"),
        pytest.param({"activity": "Abtauen"}, "activity_defrost", id="defrost"),
        pytest.param({"activity": "Bereitschaft"}, "activity_idle", id="compressor pause"),
        pytest.param({"bypass": 40.0}, "bypass", id="bypass open"),
        pytest.param({"bypass": None}, "bypass_missing", id="bypass unreadable"),
        pytest.param({"block": "An"}, "blocked", id="room thermostat"),
        pytest.param({"error": "E9001 Rücklauffühler"}, "fault", id="fault"),
        pytest.param({"error": None}, "error_missing", id="error unreadable"),
        pytest.param({"setpoint": None}, "setpoint_missing", id="setpoint unreadable"),
    ],
)
def test_frozen(profile: dict[str, Any], changes: dict[str, Any], reason: str) -> None:
    """Each condition alone freezes, with its reason."""
    evaluation = tracker(profile).evaluate(reading(profile, **changes), NOW, usable=True)

    assert not evaluation.valid
    assert reason in evaluation.frozen


def test_unusable_device(profile: dict[str, Any]) -> None:
    """Not reachable or not compatible: not valid."""
    evaluation = tracker(profile).evaluate(reading(profile), NOW, usable=False)

    assert evaluation.frozen == ("unusable",)


def test_warning_does_not_freeze(profile: dict[str, Any]) -> None:
    """A warning (W code) leaves heating valid."""
    assert tracker(profile).evaluate(reading(profile, error="W8006 Warnung Druckverlust"), NOW, usable=True).valid


@pytest.mark.parametrize(("closed_for", "valid"), [(9, False), (10, True), (11, True)])
def test_bypass_settling(profile: dict[str, Any], closed_for: int, valid: bool) -> None:
    """The bypass valve has to read 0 % for 10 minutes (from Home Assistant's last change)."""
    evaluation = tracker(profile).evaluate(reading(profile, bypass_since=NOW - closed_for * MIN), NOW, usable=True)

    assert evaluation.valid is valid
    assert ("bypass_settling" in evaluation.frozen) is not valid


def test_bypass_settling_without_last_change(profile: dict[str, Any]) -> None:
    """Without Home Assistant's time Sunflow counts from its own first reading of 0 %."""
    track = tracker(profile)
    track.evaluate(reading(profile, bypass=100.0, bypass_since=None), NOW - 20 * MIN, usable=True)

    assert not track.evaluate(reading(profile, bypass_since=None), NOW - 10 * MIN, usable=True).valid
    assert not track.evaluate(reading(profile, bypass_since=None), NOW - MIN, usable=True).valid
    assert track.evaluate(reading(profile, bypass_since=None), NOW, usable=True).valid


@pytest.mark.parametrize(("shown", "kind"), [("Warmwasserbereitung", "hot_water"), ("Abtauen", "defrost")])
@pytest.mark.parametrize(("after", "valid"), [(9, False), (11, True)])
def test_after_run(profile: dict[str, Any], shown: str, kind: str, after: int, valid: bool) -> None:
    """10 minutes after hot water or defrost heating is still frozen (the bypass closed long before here)."""
    end = NOW - after * MIN
    track = tracker(profile)
    track.evaluate(reading(profile, activity=shown, activity_since=end - 30 * MIN), end - MIN, usable=True)

    evaluation = track.evaluate(reading(profile, activity_since=end), NOW, usable=True)

    assert evaluation.valid is valid
    assert evaluation.after_run == (() if valid else (kind,))
    assert (f"after_{kind}" in evaluation.frozen) is not valid


def test_after_run_from_the_event(profile: dict[str, Any]) -> None:
    """The end of hot water seen only in a state_changed event (between two samples) counts as well."""
    track = tracker(profile)
    track.evaluate(reading(profile), NOW - 30 * MIN, usable=True)
    track.activity_changed("hot_water", "heating", NOW - 5 * MIN)

    assert track.evaluate(reading(profile, activity_since=NOW - 5 * MIN), NOW, usable=True).after_run == ("hot_water",)


def test_after_run_and_bypass_run_side_by_side(profile: dict[str, Any]) -> None:
    """The bypass closed 12 minutes ago, defrost ended 5 minutes ago: the later end counts, nothing adds up."""
    track = tracker(profile)
    track.evaluate(reading(profile, activity="Abtauen", bypass_since=NOW - 12 * MIN), NOW - 6 * MIN, usable=True)

    frozen = track.evaluate(reading(profile, activity_since=NOW - 5 * MIN, bypass_since=NOW - 12 * MIN), NOW, usable=True)
    later = track.evaluate(reading(profile, activity_since=NOW - 5 * MIN, bypass_since=NOW - 12 * MIN), NOW + 6 * MIN, usable=True)

    assert frozen.frozen == ("after_defrost",)
    assert later.valid


@pytest.mark.parametrize(("changed", "after_run"), [(5, ("unknown",)), (30, ())])
def test_after_run_unknown_after_a_restart(profile: dict[str, Any], changed: int, after_run: tuple[str, ...]) -> None:
    """Sunflow just started and the activity changed minutes ago: it may have been hot water, so wait."""
    evaluation = tracker(profile).evaluate(reading(profile, activity_since=NOW - changed * MIN), NOW, usable=True)

    assert evaluation.after_run == after_run


# The heat pump limits the flow itself (strategy fixed)


def test_small_difference_is_no_limit(profile: dict[str, Any]) -> None:
    """0.4 K below the set value is within the deadband."""
    track = tracker(profile)
    for minute in range(11):
        evaluation = track.evaluate(reading(profile, setpoint=29.6), NOW + minute * MIN, usable=True)

    assert evaluation.valid


def test_self_limit_after_five_minutes_ends_at_once(profile: dict[str, Any]) -> None:
    """0.6 K below: valid for 4 minutes, not after 6; back within 0.5 K it is valid right away."""
    track = tracker(profile)
    results = {minute: track.evaluate(reading(profile, setpoint=29.4), NOW + minute * MIN, usable=True) for minute in range(7)}
    back = track.evaluate(reading(profile, setpoint=29.6), NOW + 7 * MIN, usable=True)

    assert results[4].valid
    assert not results[6].valid
    assert results[6].self_limited
    assert results[6].frozen == ("self_limited",)
    assert back.valid
    assert not back.self_limited


def test_self_limit_counts_from_an_own_write(profile: dict[str, Any]) -> None:
    """After Sunflow wrote, the heat pump takes the new value over first: the 5 minutes start at the write."""
    track = tracker(profile)
    track.evaluate(reading(profile, setpoint=28.0), NOW, usable=True)
    track.written_at = NOW + 4 * MIN

    assert track.evaluate(reading(profile, setpoint=28.0), NOW + 8 * MIN, usable=True).valid
    assert not track.evaluate(reading(profile, setpoint=28.0), NOW + 10 * MIN, usable=True).valid


def test_self_limit_only_with_the_strategy_fixed(profile: dict[str, Any]) -> None:
    """With the weather-dependent curve Max T-Vorlauf is only a cap: the criterion does not apply."""
    track = tracker(profile)
    for minute in range(11):
        evaluation = track.evaluate(reading(profile, strategy="Witterungsgeführt", setpoint=25.0, max_setpoint=None), NOW + minute * MIN, usable=True)

    assert evaluation.valid
    assert not evaluation.self_limited
