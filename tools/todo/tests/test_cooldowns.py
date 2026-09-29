"""Cooldowns: things that refill over time and are emptied by claiming them. A plain cooldown
(Fountain, Harbour Ship: claim, then wait 8h) holds 1; others refill 1 every so often up to a max.
Its current value (0..max) can be set when adding or editing it. They live on their own tab."""

from itertools import count

import pytest

from tools.todo.core.codec import CodecError, cooldown_from_dict, parse_command, state_from_dict, state_to_dict
from tools.todo.core.commands import (
    AddBay,
    AddCooldown,
    RefillProgress,
    AddTask,
    Claim,
    Claimed,
    EditCooldown,
    MoveCooldown,
    RemoveCooldown,
    Unclaim,
    apply,
)
from tools.todo.core.model import CommandError, Cooldown, State, find_cooldown, tasks_in_bay
from tools.todo.core.preset import hide, import_item, instantiate, suggested
from tools.todo.core.schedule import full_at, is_full, level, next_refill_at
from tools.todo.core.view import build_view

from .helpers import FRI, at, run, seeded

EIGHT_HOURS = 480


def claims() -> State:
    """Fountain and Harbour Ship, both claimable (full) at FRI."""
    return run(seeded(), AddCooldown("f", "Fountain", EIGHT_HOURS), AddCooldown("h", "Harbour", EIGHT_HOURS),
               Unclaim("f"), Unclaim("h"))


def tickets(value=0) -> State:
    """Refills 1 every 2h, up to 5; added at 08:00."""
    return run(seeded(), AddCooldown("t", "Tickets", 120, capacity=5, value=value), now=at(25, 8))


# --- plain cooldowns (capacity 1) ---------------------------------------------------------

def test_new_plain_cooldown_is_at_0_by_default_and_waits_one_cycle():
    s = run(seeded(), AddCooldown("h", "Harbour", EIGHT_HOURS), now=at(25, 8))
    h = find_cooldown(s, "h")
    assert (level(h, at(25, 8)), next_refill_at(h, at(25, 8))) == (0, at(25, 16))
    assert is_full(h, at(25, 16))


def test_claim_starts_the_wait():
    s, events = apply(claims(), Claim("h"), at(25, 8))
    h = find_cooldown(s, "h")
    assert events == [Claimed("h", at(25, 8), 1)]
    assert next_refill_at(h, at(25, 9)) == full_at(h, at(25, 9)) == at(25, 16)
    assert level(h, at(25, 15, 59)) == 0
    assert is_full(h, at(25, 16))


def test_anything_can_be_claimed_early_restarting_the_wait():
    s = run(claims(), Claim("h"), now=at(25, 8))
    s, events = apply(s, Claim("h"), at(25, 10))
    assert events == [Claimed("h", at(25, 10), 0)]
    assert next_refill_at(find_cooldown(s, "h"), at(25, 11)) == at(25, 18)


def test_unclaim_makes_it_full_again():
    s = run(claims(), Claim("h"), now=at(25, 8))
    s = run(s, Unclaim("h"), now=at(25, 9))
    assert is_full(find_cooldown(s, "h"), at(25, 9))
    assert run(s, Unclaim("h")) == s  # already full: nothing to take back


# --- refilling (capacity > 1) -------------------------------------------------------------

def test_at_0_by_default_and_refills_one_at_a_time():
    t = find_cooldown(tickets(), "t")
    assert level(t, at(25, 8)) == 0
    assert level(t, at(25, 9, 59)) == 0
    assert level(t, at(25, 10)) == 1
    assert level(t, at(25, 15)) == 3
    assert next_refill_at(t, at(25, 15)) == at(25, 16)
    assert full_at(t, at(25, 15)) == at(25, 18)
    assert level(t, at(26, 8)) == 5 and is_full(t, at(26, 8))  # never more than the max


def test_can_be_added_part_way_with_the_next_one_a_full_refill_away():
    t = find_cooldown(tickets(value=3), "t")
    assert (level(t, at(25, 8)), next_refill_at(t, at(25, 8)), full_at(t, at(25, 8))) == (3, at(25, 10), at(25, 12))


def test_can_be_added_full():
    t = find_cooldown(tickets(value=5), "t")
    assert is_full(t, at(25, 8)) and t.emptied_at is None


@pytest.mark.parametrize("value", [-1, 6])
def test_value_must_be_between_0_and_capacity(value):
    with pytest.raises(CommandError, match="between 0 and 5"):
        tickets(value=value)


def test_claim_takes_everything_and_refilling_starts_over():
    s, events = apply(tickets(), Claim("t"), at(25, 15))  # 3 had refilled; the half-way 4th is lost
    assert events == [Claimed("t", at(25, 15), 3)]
    t = find_cooldown(s, "t")
    assert level(t, at(25, 15)) == 0
    assert next_refill_at(t, at(25, 15)) == at(25, 17)


def test_edit_capacity_or_speed_applies_to_the_refill_in_progress():
    s = run(tickets(), EditCooldown("t", capacity=2), now=at(25, 8))
    assert level(find_cooldown(s, "t"), at(25, 15)) == 2
    s = run(s, EditCooldown("t", minutes=60, capacity=10))
    assert level(find_cooldown(s, "t"), at(25, 15)) == 7


def test_edit_sets_the_current_value():
    s = run(tickets(), EditCooldown("t", value=4), now=at(25, 9))
    t = find_cooldown(s, "t")
    assert (level(t, at(25, 9)), next_refill_at(t, at(25, 9))) == (4, at(25, 11))
    assert is_full(find_cooldown(run(s, EditCooldown("t", value=5), now=at(25, 9)), "t"), at(25, 9))
    assert level(find_cooldown(run(s, EditCooldown("t", value=0), now=at(25, 9)), "t"), at(25, 10)) == 0


def test_edit_keeps_refill_progress_when_the_value_is_unchanged():
    """The edit dialog always sends the value; re-saving the same one must not restart the wait."""
    s = run(tickets(), EditCooldown("t", title="Arena Tickets", value=1), now=at(25, 11))  # 1 so far, 2nd due 12:00
    t = find_cooldown(s, "t")
    assert t.title == "Arena Tickets" and next_refill_at(t, at(25, 11)) == at(25, 12)


def test_edit_value_is_checked_against_the_new_capacity():
    with pytest.raises(CommandError):
        run(tickets(), EditCooldown("t", value=6))
    with pytest.raises(CommandError):
        run(tickets(), EditCooldown("t", capacity=3, value=4))
    s = run(tickets(), EditCooldown("t", capacity=3, value=3), now=at(25, 9))
    assert is_full(find_cooldown(s, "t"), at(25, 9))


# --- add / edit / remove / move -----------------------------------------------------------

def test_add_edit_remove_move():
    s = run(claims(), EditCooldown("h", title="Harbour Ship", minutes=90))
    assert (find_cooldown(s, "h").title, find_cooldown(s, "h").minutes) == ("Harbour Ship", 90)
    s = run(s, AddCooldown("x", "Extra", 60), MoveCooldown("x", 0))
    assert [c.id for c in sorted(s.cooldowns, key=lambda c: c.position)] == ["x", "f", "h"]
    s = run(s, RemoveCooldown("x"))
    assert {c.id: c.position for c in s.cooldowns} == {"f": 0, "h": 1}


@pytest.mark.parametrize("cmd", [
    AddCooldown("z", "  ", 60),
    AddCooldown("z", "Zero", 0),
    AddCooldown("z", "Holds nothing", 60, capacity=0),
    AddCooldown("z", "Over the max", 60, value=2),
    AddCooldown("f", "Duplicate id", 60),
    AddCooldown("main", "Clashes with a bay id", 60),
    EditCooldown("h", minutes=-5),
    EditCooldown("h", capacity=0),
    EditCooldown("h", value=2),
    Claim("nope"),
])
def test_rejects_bad_commands(cmd):
    with pytest.raises(CommandError):
        run(claims(), cmd)


# --- view ---------------------------------------------------------------------------------

def test_view_lists_full_first():
    s = run(claims(), AddCooldown("t", "Tickets", 120, capacity=5), Claim("f"), now=at(25, 8))
    v = build_view(s, at(25, 11))
    assert [c["id"] for c in v["cooldowns"]] == ["h", "f", "t"]
    h, f, t = v["cooldowns"]
    assert (h["level"], h["full"], h["next_at"], h["label"]) == (1, True, None, "Every 8h")
    assert (f["level"], f["full"], f["next_at"]) == (0, False, "2026-09-25T16:00:00+00:00")
    assert (t["level"], t["capacity"], t["label"]) == (1, 5, "+1 every 2h · max 5")
    assert (t["next_at"], t["full_at"]) == ("2026-09-25T12:00:00+00:00", "2026-09-25T18:00:00+00:00")
    assert v["cooldowns_full"] == 1


def test_cooldowns_are_not_in_bays():
    v = build_view(claims(), FRI)
    assert v["bays"][0]["tasks"] == [] and v["bays"][0]["progress"] == {"done": 0, "total": 0}


# --- codec --------------------------------------------------------------------------------

def test_round_trip():
    s = run(claims(), Claim("f"), AddCooldown("t", "Tickets", 120, capacity=5, value=2))
    assert state_from_dict(state_to_dict(s)) == s


def test_parse_commands():
    assert parse_command({"type": "add_cooldown", "title": "F", "minutes": 480}, "new") == AddCooldown("new", "F", 480)
    assert parse_command({"type": "add_cooldown", "title": "T", "minutes": 120, "capacity": 5, "value": 2},
                         "new") == AddCooldown("new", "T", 120, 5, 2)
    assert parse_command({"type": "edit_cooldown", "cooldown_id": "t", "value": 0}, "_") == EditCooldown("t", value=0)
    with pytest.raises(CodecError):
        parse_command({"type": "add_cooldown", "title": "T", "minutes": 120, "value": "none"}, "_")
    assert parse_command({"type": "edit_cooldown", "cooldown_id": "h", "capacity": 3}, "_") == EditCooldown("h", capacity=3)
    assert parse_command({"type": "claim", "cooldown_id": "h"}, "_") == Claim("h")
    assert parse_command({"type": "unclaim", "cooldown_id": "h"}, "_") == Unclaim("h")
    assert parse_command({"type": "move_cooldown", "cooldown_id": "h", "index": 0}, "_") == MoveCooldown("h", 0)
    with pytest.raises(CodecError):
        parse_command({"type": "claim", "task_id": "h"}, "_")


def test_reads_cooldowns_saved_before_refills():
    old = {"id": "f", "title": "Fountain", "position": 0, "minutes": 480, "early": True,
           "claimed_at": "2026-09-25T08:00:00+00:00", "origin_id": "pf"}
    assert cooldown_from_dict(old) == Cooldown("f", "Fountain", 0, 480, emptied_at=at(25, 8), origin_id="pf")


def _legacy_task(id, bay_id, title, position, recurrence, completed_at=None, origin_id=None):
    return {
        "id": id, "bay_id": bay_id, "title": title, "position": position, "enabled": True,
        "recurrence": recurrence, "completed_at": completed_at, "timer": None, "reminder_at": None,
        "categories": [], "origin_id": origin_id,
    }


def test_old_snapshots_move_cooldown_tasks_to_the_cooldowns_tab():
    """Snapshots from before the Cooldowns tab stored them as tasks with a "cooldown" repeat."""
    old = {
        "bays": [
            {"id": "main", "name": "Main", "position": 0},
            {"id": "claims", "name": "Claims", "position": 1},
            {"id": "shops", "name": "Shops", "position": 2},
        ],
        "tasks": [
            _legacy_task("a", "main", "Bounties", 0, {"kind": "daily"}),
            _legacy_task("x", "main", "Stray", 1, {"kind": "cooldown", "minutes": 60, "early": False}),
            _legacy_task("b", "main", "Shop", 2, {"kind": "daily"}),
            _legacy_task("f", "claims", "Fountain", 0, {"kind": "cooldown", "minutes": 480, "early": True},
                         completed_at="2026-09-25T08:00:00+00:00", origin_id="pf"),
            _legacy_task("h", "claims", "Harbour", 1, {"kind": "cooldown", "minutes": 480}),
            _legacy_task("s", "shops", "Arena Shop", 0, {"kind": "daily"}),
        ],
    }
    s = state_from_dict(old)
    assert [(b.id, b.position) for b in s.bays] == [("main", 0), ("shops", 1)]  # emptied bay dropped
    assert [(t.id, t.position) for t in tasks_in_bay(s, "main")] == [("a", 0), ("b", 1)]
    assert sorted(s.cooldowns, key=lambda c: c.position) == [
        Cooldown("x", "Stray", 0, 60),
        Cooldown("f", "Fountain", 1, 480, emptied_at=at(25, 8), origin_id="pf"),
        Cooldown("h", "Harbour", 2, 480),
    ]
    assert state_from_dict(state_to_dict(s)) == s


def test_old_group_with_a_cooldown_repeat_stays_a_group():
    old = {
        "bays": [{"id": "main", "name": "Main", "position": 0}],
        "tasks": [
            _legacy_task("g", "main", "Group", 0, {"kind": "cooldown", "minutes": 60}),
            _legacy_task("c", "main", "Step", 0, {"kind": "daily"}) | {"parent_id": "g"},
        ],
    }
    s = state_from_dict(old)
    assert s.cooldowns == () and {t.id for t in s.tasks} == {"g", "c"}


# --- preset -------------------------------------------------------------------------------

def _ids(prefix="u"):
    return (f"{prefix}{i}" for i in count())


def _preset() -> State:
    return run(State(), AddBay("pmain", "Main"), AddTask("pshop", "pmain", "Shop"),
               AddCooldown("pf", "Fountain", 480), now=at(20))


def test_new_users_get_the_presets_cooldowns():
    user = instantiate(_preset(), _ids())
    [f] = user.cooldowns
    assert (f.title, f.minutes, f.capacity, f.origin_id) == ("Fountain", 480, 1, "pf")
    assert suggested(user, _preset()) == []


def test_copies_hold_what_the_preset_holds():
    """The admin sets what new copies have by setting the preset's current value."""
    p = run(_preset(), AddCooldown("pt", "Tickets", 120, capacity=5, value=2), AddCooldown("pfull", "Full", 60, value=1),
            now=at(25, 8))
    copies = {c.origin_id: c for c in instantiate(p, _ids()).cooldowns}
    assert (level(copies["pt"], at(25, 8)), next_refill_at(copies["pt"], at(25, 8))) == (2, at(25, 10))
    assert is_full(copies["pfull"], at(25, 8))


def test_new_preset_cooldowns_are_suggested_and_importable():
    user = instantiate(_preset(), _ids())
    p = run(_preset(), AddCooldown("ph", "Harbour Ship", 480))
    [s] = suggested(user, p)
    assert (s.id, s.title, s.bay_name, s.steps) == ("ph", "Harbour Ship", "Cooldowns", 0)
    user = import_item(user, p, "ph", _ids("n"))
    assert [(c.title, c.position, c.origin_id) for c in sorted(user.cooldowns, key=lambda c: c.position)] == [
        ("Fountain", 0, "pf"), ("Harbour Ship", 1, "ph"),
    ]
    assert suggested(user, p) == []
    assert suggested(hide(instantiate(_preset(), _ids()), "ph"), p) == []


def test_view_sorts_soonest_ready_first():
    s = run(claims(), AddCooldown("t", "Tickets", 60, capacity=3), AddCooldown("x", "Extra", 600), now=at(25, 6))
    s = run(s, Claim("f"), now=at(25, 9))   # Fountain ready 17:00
    s = run(s, Claim("h"), now=at(25, 8))   # Harbour ready 16:00, though it's stored after the Fountain
    v = build_view(s, at(25, 10))           # Tickets full at 09:00 (ready now); Extra ready 16:00, stored last
    assert [c["id"] for c in v["cooldowns"]] == ["t", "h", "x", "f"]



# --- progress towards the next refill (optional, when adding or editing) -------------------

def test_add_with_time_left():
    """Fountain in the game says "ready in 3h 10m": it's at 0 with 3h 10m to go."""
    s = run(seeded(), AddCooldown("f", "Fountain", EIGHT_HOURS, progress=RefillProgress(190, remaining=True)),
            now=at(25, 8))
    f = find_cooldown(s, "f")
    assert (level(f, at(25, 8)), next_refill_at(f, at(25, 8))) == (0, at(25, 11, 10))


def test_add_with_time_passed_and_a_value():
    s = run(seeded(), AddCooldown("t", "Tickets", 120, capacity=5, value=2, progress=RefillProgress(30)), now=at(25, 8))
    t = find_cooldown(s, "t")
    assert (level(t, at(25, 8)), next_refill_at(t, at(25, 8)), full_at(t, at(25, 8))) == (2, at(25, 9, 30), at(25, 13, 30))


def test_edit_progress_keeps_the_current_value_unless_given():
    s = run(tickets(), now=at(25, 8))  # 0/5, next at 10:00
    s = run(s, EditCooldown("t", progress=RefillProgress(15, remaining=True)), now=at(25, 11))  # 1/5 by now
    t = find_cooldown(s, "t")
    assert (level(t, at(25, 11)), next_refill_at(t, at(25, 11))) == (1, at(25, 11, 15))
    s = run(s, EditCooldown("t", value=4, progress=RefillProgress(0)), now=at(25, 11))
    t = find_cooldown(s, "t")
    assert (level(t, at(25, 11)), next_refill_at(t, at(25, 11))) == (4, at(25, 13))


def test_progress_is_checked_against_the_new_refill_time():
    s = run(claims(), Claim("h"), EditCooldown("h", minutes=60, progress=RefillProgress(45, remaining=True)), now=at(25, 8))
    assert next_refill_at(find_cooldown(s, "h"), at(25, 8)) == at(25, 8, 45)


@pytest.mark.parametrize("progress", [
    RefillProgress(480, remaining=True),  # not less than the refill time
    RefillProgress(0, remaining=True),    # nothing left means it has already refilled
    RefillProgress(480),
    RefillProgress(600),
])
def test_progress_must_be_less_than_the_refill_time(progress):
    with pytest.raises(CommandError, match="refill time"):
        run(seeded(), AddCooldown("f", "Fountain", EIGHT_HOURS, progress=progress))


def test_no_progress_to_set_when_full():
    with pytest.raises(CommandError, match="full"):
        run(seeded(), AddCooldown("t", "Tickets", 120, capacity=5, value=5, progress=RefillProgress(30)))
    with pytest.raises(CommandError, match="full"):
        run(claims(), EditCooldown("h", progress=RefillProgress(30)))  # already full, no value given


def test_parse_progress():
    parse = lambda progress: parse_command({"type": "add_cooldown", "title": "F", "minutes": 480, "progress": progress}, "n")
    assert parse({"remaining": 190}).progress == RefillProgress(190, remaining=True)
    assert parse({"elapsed": 30}).progress == RefillProgress(30)
    assert parse(None).progress is None
    for bad in ({"left": 5}, {"elapsed": 5, "remaining": 5}, {"elapsed": "5"}, {"remaining": -1}, 30):
        with pytest.raises(CodecError):
            parse(bad)
