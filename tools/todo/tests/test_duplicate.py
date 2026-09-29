"""Duplicate: a fresh copy right below the original, among the same siblings."""

import pytest

from tools.todo.core.codec import parse_command
from tools.todo.core.commands import (
    AddTask,
    Complete,
    DuplicateTask,
    SetEnabled,
    SetProgress,
    SetReminder,
    StartTimer,
)
from tools.todo.core.model import CommandError, Recurrence, find_task, subtasks, tasks_in_bay

from .helpers import at, run, seeded

WINDOWS = Recurrence("windows", windows=((0, 30), (120, 150)))


def order(s, parent_id=None):
    return [t.id for t in tasks_in_bay(s, "main", parent_id)]


def test_copy_goes_right_below_and_starts_fresh():
    s = run(
        seeded("a", "b", "c"),
        AddTask("po", "main", "Post Office", WINDOWS, categories=("town",), target=3, parent_id=None),
    )
    s = run(s, SetProgress("po", 2), StartTimer("po", 60), SetReminder("po", at(25, 20)), now=at(25, 0, 10))
    s = run(s, DuplicateTask("b", "b2"), DuplicateTask("po", "po2"), now=at(25, 0, 15))
    assert order(s) == ["a", "b", "b2", "c", "po", "po2"]
    copy = find_task(s, "po2")
    assert (copy.title, copy.recurrence, copy.categories, copy.target) == ("Post Office (copy)", WINDOWS, ("town",), 3)
    assert (copy.completed_at, copy.progress, copy.timer, copy.reminder_at) == (None, 0, None, None)
    assert find_task(s, "po").progress == 2  # the original is untouched


def test_subtask_copy_stays_under_the_same_parent():
    s = run(
        seeded("ts", "other"),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        AddTask("cake", "main", "Cake", Recurrence("daily"), parent_id="ts"),
        Complete("mail"),
        DuplicateTask("mail", "mail2"),
    )
    assert order(s, "ts") == ["mail", "mail2", "cake"]
    copy = find_task(s, "mail2")
    assert (copy.parent_id, copy.title, copy.completed_at) == ("ts", "Mail (copy)", None)
    assert order(s) == ["ts", "other"]  # top level unchanged


def test_group_is_copied_with_all_its_steps():
    s = run(
        seeded("ts", "other"),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        AddTask("cake", "main", "Cake", Recurrence("daily"), parent_id="ts"),
        SetEnabled("cake", False),
        Complete("ts"),
        DuplicateTask("ts", "ts2"),
    )
    assert order(s) == ["ts", "ts2", "other"]
    steps = subtasks(s, find_task(s, "ts2"))
    assert [(c.title, c.enabled, c.completed_at, c.position) for c in steps] == [
        ("Mail", True, None, 0), ("Cake", False, None, 1),  # steps keep their names and opt-out
    ]
    assert {c.id for c in steps}.isdisjoint({"mail", "cake"})
    assert len(subtasks(s, find_task(s, "ts"))) == 2  # the original keeps its own steps


def test_preset_origin_is_not_copied():
    from dataclasses import replace
    s = seeded("a")
    s = replace(s, tasks=tuple(replace(t, origin_id="p1") for t in s.tasks))
    assert find_task(run(s, DuplicateTask("a", "a2")), "a2").origin_id is None


def test_errors_and_parsing():
    with pytest.raises(CommandError):
        run(seeded("a"), DuplicateTask("nope", "x"))
    with pytest.raises(CommandError):
        run(seeded("a", "b"), DuplicateTask("a", "b"))  # id clash
    assert parse_command({"type": "duplicate_task", "task_id": "a"}, "fresh") == DuplicateTask("a", "fresh")
