import pytest

from tools.todo.core.commands import (
    AddBay,
    AddTask,
    ClearReminder,
    Complete,
    Completed,
    EditTask,
    MoveBay,
    MoveTask,
    MoveToBottom,
    MoveToTop,
    RemoveBay,
    RemoveTask,
    RenameBay,
    SetEnabled,
    SetReminder,
    StartTimer,
    StopTimer,
    Uncomplete,
    apply,
)
from tools.todo.core.model import CommandError, Recurrence, State, Timer, bays_in_order, find_task, tasks_in_bay

from .helpers import FRI, at, run, seeded


def titles(state: State, bay="main") -> list[str]:
    return [t.title for t in tasks_in_bay(state, bay)]


def test_add_bay_and_tasks_append_in_order():
    s = seeded("a", "b", "c")
    assert titles(s) == ["a", "b", "c"]
    assert [t.position for t in tasks_in_bay(s, "main")] == [0, 1, 2]


def test_duplicate_ids_rejected():
    s = seeded("a")
    with pytest.raises(CommandError):
        run(s, AddTask("a", "main", "again"))
    with pytest.raises(CommandError):
        run(s, AddBay("main", "again"))


def test_empty_names_rejected():
    with pytest.raises(CommandError):
        run(State(), AddBay("x", "   "))


def test_unknown_ids_rejected():
    with pytest.raises(CommandError):
        run(seeded("a"), Complete("nope"))
    with pytest.raises(CommandError):
        run(seeded("a"), AddTask("z", "nobay", "z"))


def test_rename_and_remove_bay():
    s = run(seeded("a"), AddBay("b2", "Weekly"), RenameBay("main", "Dailies"))
    assert [b.name for b in bays_in_order(s)] == ["Dailies", "Weekly"]
    s = run(s, RemoveBay("main"))
    assert [(b.name, b.position) for b in s.bays] == [("Weekly", 0)]
    assert s.tasks == ()


def test_move_bay():
    s = run(State(), AddBay("x", "X"), AddBay("y", "Y"), AddBay("z", "Z"), MoveBay("z", 0))
    assert [b.id for b in bays_in_order(s)] == ["z", "x", "y"]


def test_edit_task_changes_only_given_fields():
    s = run(seeded("a"), EditTask("a", categories=("arena",)))
    t = find_task(s, "a")
    assert (t.title, t.recurrence.kind, t.categories) == ("a", "daily", ("arena",))
    s = run(s, EditTask("a", title="Arena", recurrence=Recurrence("weekly", weekday=2)))
    t = find_task(s, "a")
    assert (t.title, t.recurrence.weekday, t.categories) == ("Arena", 2, ("arena",))


def test_complete_and_uncomplete():
    s, events = apply(seeded("a"), Complete("a"), FRI)
    assert find_task(s, "a").completed_at == FRI
    assert events == [Completed(("a",), FRI)]
    s = run(s, Uncomplete("a"))
    assert find_task(s, "a").completed_at is None


def test_complete_when_already_done_is_noop():
    s = run(seeded("a"), Complete("a"), now=at(25, 9))
    s2, events = apply(s, Complete("a"), at(25, 10))
    assert s2 == s and events == []


def test_uncomplete_when_not_done_is_noop():
    s = run(seeded("a"), Complete("a"), now=at(24))  # yesterday's completion has expired
    s2, events = apply(s, Uncomplete("a"), at(25))
    assert s2 == s and events == []


def test_recurring_task_can_be_completed_again_after_reset():
    s = run(seeded("a"), Complete("a"), now=at(24))
    s = run(s, Complete("a"), now=at(25))
    assert find_task(s, "a").completed_at == at(25)


def test_move_within_bay():
    s = run(seeded("a", "b", "c", "d"), MoveTask("d", "main", 1))
    assert titles(s) == ["a", "d", "b", "c"]
    s = run(s, MoveTask("a", "main", 99))
    assert titles(s) == ["d", "b", "c", "a"]


def test_move_to_top_and_bottom():
    s = run(seeded("a", "b", "c"), MoveToTop("c"))
    assert titles(s) == ["c", "a", "b"]
    s = run(s, MoveToBottom("c"))
    assert titles(s) == ["a", "b", "c"]


def test_move_between_bays_renumbers_both():
    s = run(seeded("a", "b", "c"), AddBay("w", "Weekly"), AddTask("x", "w", "x"), MoveTask("b", "w", 0))
    assert titles(s) == ["a", "c"]
    assert titles(s, "w") == ["b", "x"]
    assert [t.position for t in tasks_in_bay(s, "main")] == [0, 1]


def test_remove_task_renumbers():
    s = run(seeded("a", "b", "c"), RemoveTask("b"))
    assert [(t.title, t.position) for t in tasks_in_bay(s, "main")] == [("a", 0), ("c", 1)]


def test_set_enabled():
    s = run(seeded("a"), SetEnabled("a", False))
    assert find_task(s, "a").enabled is False


def test_timer_start_stop():
    s = run(seeded("a"), StartTimer("a", 300))
    assert find_task(s, "a").timer == Timer(FRI, 300)
    assert find_task(run(s, StopTimer("a")), "a").timer is None
    with pytest.raises(CommandError):
        run(s, StartTimer("a", 0))


def test_reminder_set_clear():
    s = run(seeded("a"), SetReminder("a", at(25, 18)))
    assert find_task(s, "a").reminder_at == at(25, 18)
    assert find_task(run(s, ClearReminder("a")), "a").reminder_at is None


def test_apply_does_not_mutate_input():
    s = seeded("a")
    before = s
    run(s, Complete("a"), MoveToBottom("a"), RemoveTask("a"))
    assert s == before and find_task(s, "a").completed_at is None
