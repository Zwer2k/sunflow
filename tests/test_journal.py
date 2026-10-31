"""Tests for the log of Sunflow's writes and of changes others made."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sunflow.control.controller import PlanController, TakeoverMarker
from sunflow.control.journal import ControlJournal
from sunflow.devices.discovery import Snapshot
from sunflow.devices.executor import PlannedCall
from test_control import NOW, FakeDevice, _config, _plan

LEVEL = "select.heizstab_stufe"
SUNFLOW_USER = "sunflow-user"


def _state(value: str, context: dict[str, Any] | None = None, entity_id: str = LEVEL, name: str = "Heizstab Stufe") -> dict[str, Any]:
    return {"entity_id": entity_id, "state": value, "attributes": {"friendly_name": name}, "context": context or {}}


async def _observe(journal: ControlJournal, *states: dict[str, Any], at: datetime = NOW) -> None:
    for minute, state in enumerate(states):
        others = {"automation.ueberschuss": _state("on", {"id": "auto-ctx"}, "automation.ueberschuss", "Überschuss")}
        await journal.observe("ww", "Warmwasser", [LEVEL], {LEVEL: state} | others, at + timedelta(minutes=minute))


@pytest.mark.parametrize(
    ("context", "source"),
    [
        pytest.param({"user_id": "u1"}, "Benutzer „Jurij“ – oder ein Programm mit dessen Zugangstoken (z. B. evcc, Node-RED)", id="user"),
        pytest.param({"parent_id": "auto-ctx"}, "Automation „Überschuss“", id="automation"),
        pytest.param({"parent_id": "gone"}, "eine Automation, ein Skript oder eine Szene", id="unknown-automation"),
        pytest.param({}, "das Gerät bzw. seine Integration selbst", id="device-itself"),
    ],
)
async def test_change_by_others_names_who_did_it(tmp_path: Path, context: dict[str, Any], source: str) -> None:
    """A change Sunflow did not make is logged with what Home Assistant's context tells."""
    journal = ControlJournal(tmp_path)
    journal.own_user, journal.users = SUNFLOW_USER, {"u1": "Jurij"}

    await _observe(journal, _state("off"), _state("2,8kW", context))

    (entry,) = journal.entries(NOW - timedelta(hours=1))
    assert (entry["kind"], entry["old"], entry["new"], entry["name"]) == ("extern", "off", "2,8kW", "Heizstab Stufe")
    assert entry["source"].startswith(source)


@pytest.mark.parametrize(
    ("own_user", "states"),
    [
        pytest.param(SUNFLOW_USER, [_state("off"), _state("2,8kW", {"user_id": SUNFLOW_USER})], id="own-user"),
        # Without the own user known: the value Sunflow wrote a moment ago
        pytest.param(None, [_state("off"), _state("2,8kW")], id="own-value"),
        pytest.param(None, [_state("off"), _state("unavailable"), _state("off")], id="unreadable"),
        pytest.param(None, [_state("off")], id="first-seen"),
    ],
)
async def test_own_and_no_changes_are_not_logged_as_foreign(tmp_path: Path, own_user: str | None, states: list[dict[str, Any]]) -> None:
    """Sunflow's own write, a read error and the first look are no foreign change."""
    journal = ControlJournal(tmp_path)
    journal.own_user = own_user
    call = PlannedCall(domain="select", service="select_option", data={"option": "2,8kW"}, entity_id=LEVEL, storage="volatile", write="always", status="done")
    journal.wrote("ww", "Warmwasser", "charge", 2800, "nach Plan", [call], NOW)

    await _observe(journal, *states)

    assert [e["kind"] for e in journal.entries(NOW - timedelta(hours=1))] == ["sunflow"]


async def test_entities_sunflow_wrote_are_watched_too(tmp_path: Path) -> None:
    """Values that are no role (e.g. the passive mode numbers) are watched once Sunflow wrote them."""
    journal = ControlJournal(tmp_path)
    call = PlannedCall(domain="number", service="set_value", data={"value": 0}, entity_id="number.grid", storage="volatile", write="always", status="done")
    journal.wrote("bat", "Sofar", "auto", None, "nach Plan", [call], NOW - timedelta(hours=1))
    for minute, value in enumerate(("0", "-200")):
        await journal.observe("bat", "Sofar", [], {"number.grid": _state(value, entity_id="number.grid")}, NOW + timedelta(minutes=minute))

    assert journal.entries(NOW - timedelta(minutes=5))[0]["new"] == "-200"


def test_kept_across_restarts_newest_first(tmp_path: Path) -> None:
    """The file holds the entries; old ones are left out when reading."""
    now = datetime.now(UTC)
    journal = ControlJournal(tmp_path)
    journal.note("bat", "Sofar", "take_over", "alt", now - timedelta(days=20))
    journal.note("bat", "Sofar", "take_over", "vorher", now - timedelta(minutes=2))
    journal.note("ww", "Warmwasser", "charge", "jetzt", now)

    again = ControlJournal(tmp_path)

    assert [e["reason"] for e in again.entries(now - timedelta(days=30))] == ["jetzt", "vorher"]
    assert [e["reason"] for e in again.entries(now - timedelta(days=30), "bat")] == ["vorher"]


async def test_controller_logs_its_writes_with_the_reason(tmp_path: Path) -> None:
    """Taking over, every write with action and reason, and handing back."""
    device = FakeDevice()
    journal = ControlJournal(tmp_path)
    controller = PlanController(
        TakeoverMarker(tmp_path), lambda d: replace(device.access(), journal=journal), lambda: {"sofar": {"capabilities": ["battery_window"], "roles": {}}}, journal
    )
    snapshot = Snapshot(entries=[], devices=[], entities=[], states={}, energy_prefs=None)

    await controller.tick(_config(), _plan(), NOW, snapshot, NOW)
    await controller.tick(_config(enabled=False), _plan(), NOW, snapshot, NOW + timedelta(minutes=1))

    entries = [(e["action"], e.get("power_w"), e["reason"]) for e in reversed(journal.entries(NOW - timedelta(hours=1)))]
    assert entries == [
        ("take_over", None, "Steuermodus eingeschaltet (vorher „Self Use“)"),
        ("force_charge", 3000, "nach Plan"),
        ("auto", None, "an das Gerät zurückgegeben"),
        ("mode", None, "an das Gerät zurückgegeben: Modus wieder „Self Use“"),
    ]
    assert journal.entries(NOW - timedelta(hours=1))[2]["calls"] == [
        {"entity_id": "number.min", "service": "set_value", "data": {"value": 3000}, "status": "done"}
    ]
