import pytest

from tools.todo.core import commands as c
from tools.todo.core.codec import CodecError, parse_command, state_from_dict, state_to_dict
from tools.todo.core.model import Recurrence

from .helpers import at, run, seeded


def test_state_round_trip():
    s = run(
        seeded("a", "b", recurrence=Recurrence("days_of_week", days=frozenset({4, 6}))),
        c.Complete("a"),
        c.StartTimer("b", 60),
        c.SetReminder("a", at(25, 20)),
        c.EditTask("b", categories=("guild", "shop"), recurrence=Recurrence("weekly", weekday=1)),
        c.AddCooldown("f", "Fountain", 480),
        c.AddCooldown("t", "Tickets", 120, capacity=5, value=2),
        c.Claim("f"),
    )
    assert state_from_dict(state_to_dict(s)) == s


def test_weekly_active_days_codec():
    from tools.todo.core.codec import recurrence_from_dict, recurrence_to_dict
    r = recurrence_from_dict({"kind": "weekly", "weekday": 0, "days": [0, 1, 2, 3, 4, 5]})
    assert r == Recurrence("weekly", weekday=0, days=frozenset(range(6)))
    assert recurrence_from_dict(recurrence_to_dict(r)) == r
    assert recurrence_to_dict(Recurrence("weekly", weekday=3)) == {"kind": "weekly", "weekday": 3}
    assert recurrence_from_dict({"kind": "weekly", "weekday": 0, "days": list(range(7))}).days == frozenset()


def test_add_commands_use_fresh_id():
    cmd = parse_command({"type": "add_task", "bay_id": "b", "title": "Arena", "task_id": "evil"}, "fresh")
    assert cmd == c.AddTask("fresh", "b", "Arena")
    assert parse_command({"type": "add_bay", "name": "X"}, "fresh") == c.AddBay("fresh", "X")


def test_parse_recurrence_and_categories():
    cmd = parse_command({
        "type": "add_task", "bay_id": "b", "title": "Ventures",
        "recurrence": {"kind": "days_of_week", "days": [4, 5, 6]},
        "categories": [" lvl41 ", "lvl41", ""],
    }, "id")
    assert cmd.recurrence == Recurrence("days_of_week", days=frozenset({4, 5, 6}))
    assert cmd.categories == ("lvl41",)


def test_parse_edit_partial():
    assert parse_command({"type": "edit_task", "task_id": "t", "title": "New"}, "_") == c.EditTask("t", "New")


def test_parse_reminder_requires_timezone():
    assert parse_command({"type": "set_reminder", "task_id": "t", "at": "2026-09-25T20:00:00Z"}, "_") \
        == c.SetReminder("t", at(25, 20))
    with pytest.raises(CodecError):
        parse_command({"type": "set_reminder", "task_id": "t", "at": "2026-09-25T20:00:00"}, "_")


@pytest.mark.parametrize("payload", [
    None,
    [],
    {"type": "nope"},
    {"type": "complete"},
    {"type": "complete", "task_id": 5},
    {"type": "add_bay", "name": "  "},
    {"type": "add_bay", "name": "x" * 500},
    {"type": "start_timer", "task_id": "t", "seconds": 0},
    {"type": "start_timer", "task_id": "t", "seconds": True},
    {"type": "set_enabled", "task_id": "t", "enabled": "yes"},
    {"type": "move_task", "task_id": "t", "bay_id": "b", "index": -1},
    {"type": "link", "task_ids": ["a", "b"]},  # linking was removed
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "hourly"}},
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "weekly", "weekday": 7}},
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "days_of_week", "days": []}},
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "cooldown", "minutes": 60}},  # own tab now
    {"type": "add_cooldown", "title": "t", "minutes": 0},
    {"type": "add_cooldown", "title": "t", "minutes": 60, "capacity": 0},
    {"type": "add_cooldown", "title": "t", "minutes": 60, "value": -1},
    {"type": "claim_early", "task_id": "t"},  # claiming early is just `claim` now
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "interval", "every": 1, "anchor": "2026-09-24"}},
    {"type": "add_task", "bay_id": "b", "title": "t", "recurrence": {"kind": "interval", "every": 3, "anchor": "soon"}},
])
def test_rejects_bad_payloads(payload):
    with pytest.raises(CodecError):
        parse_command(payload, "_")
