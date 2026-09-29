import pytest

from tools.todo.core.commands import AddBay, AddTask, Complete, MoveTask, MoveToBottom, RemoveTask, SetEnabled, Uncomplete
from tools.todo.core.model import CommandError, Recurrence, find_task, subtasks, tasks_in_bay
from tools.todo.core.schedule import is_done, item_done

from .helpers import FRI, at, run, seeded

DAILY = Recurrence("daily")


def town_square():
    """Top-level: ts (with a, b, c), x."""
    return run(
        seeded("ts", "x"),
        *(AddTask(i, "main", i, DAILY, parent_id="ts") for i in ("a", "b", "c")),
    )


def done_ids(state, now=FRI):
    return {t.id for t in state.tasks if is_done(t, now)}


def test_subtasks_are_not_top_level_siblings():
    s = town_square()
    assert [t.id for t in tasks_in_bay(s, "main")] == ["ts", "x"]
    assert [t.id for t in subtasks(s, find_task(s, "ts"))] == ["a", "b", "c"]


def test_ticking_parent_ticks_all_subtasks():
    s = run(town_square(), Complete("ts"))
    assert done_ids(s) == {"a", "b", "c"}
    assert item_done(s, find_task(s, "ts"), FRI)


def test_parent_done_only_when_all_subtasks_done():
    s = run(town_square(), Complete("a"), Complete("b"))
    assert not item_done(s, find_task(s, "ts"), FRI)
    s = run(s, Complete("c"))
    assert item_done(s, find_task(s, "ts"), FRI)


def test_ticking_partly_done_parent_keeps_existing_completion_times():
    s = run(town_square(), Complete("a"), now=at(25, 9))
    s = run(s, Complete("ts"), now=at(25, 10))
    assert find_task(s, "a").completed_at == at(25, 9)
    assert find_task(s, "b").completed_at == at(25, 10)


def test_unticking_parent_unticks_all():
    s = run(town_square(), Complete("ts"), Uncomplete("ts"))
    assert done_ids(s) == set()


def test_opted_out_subtask_is_ignored():
    s = run(town_square(), SetEnabled("c", False), Complete("ts"))
    assert done_ids(s) == {"a", "b"}
    assert item_done(s, find_task(s, "ts"), FRI)


def test_parent_resets_with_subtasks():
    s = run(town_square(), Complete("ts"), now=at(24))
    assert not item_done(s, find_task(s, "ts"), at(25))


def test_only_one_level_and_same_bay():
    s = town_square()
    with pytest.raises(CommandError):
        run(s, AddTask("deep", "main", "deep", parent_id="a"))
    with pytest.raises(CommandError):
        run(s, AddBay("w", "W"), AddTask("z", "w", "z", parent_id="ts"))


def test_reorder_within_subtasks():
    s = run(town_square(), MoveToBottom("a"), MoveTask("c", "main", 0))
    assert [t.id for t in subtasks(s, find_task(s, "ts"))] == ["c", "b", "a"]
    assert [t.id for t in tasks_in_bay(s, "main")] == ["ts", "x"]


def test_moving_parent_to_other_bay_takes_subtasks():
    s = run(town_square(), AddBay("w", "W"), MoveTask("ts", "w", 0))
    assert [t.id for t in tasks_in_bay(s, "w")] == ["ts"]
    assert {t.bay_id for t in subtasks(s, find_task(s, "ts"))} == {"w"}


def test_moving_subtask_to_other_bay_makes_it_top_level():
    s = run(town_square(), AddBay("w", "W"), MoveTask("b", "w", 0))
    assert find_task(s, "b").parent_id is None
    assert [t.position for t in subtasks(s, find_task(s, "ts"))] == [0, 1]


def test_removing_parent_removes_subtasks():
    s = run(town_square(), RemoveTask("ts"))
    assert [t.id for t in s.tasks] == ["x"]
    assert find_task(s, "x").position == 0
