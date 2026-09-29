import pytest

from tools.todo.core.codec import parse_command, state_from_dict, state_to_dict
from tools.todo.core.commands import AddTask, Complete, EditTask, SetProgress, Uncomplete
from tools.todo.core.model import CommandError, Recurrence, find_task
from tools.todo.core.schedule import is_done, item_done, progress
from tools.todo.core.view import build_view, counter_style

from .helpers import FRI, at, run, seeded

DAILY = Recurrence("daily")


def ads():
    """'ads' parent with a plain 'gacha' and a 5-count 'normal' subtask, plus a 45-count 'tree'."""
    return run(
        seeded("ads"),
        AddTask("gacha", "main", "Gacha", DAILY, parent_id="ads"),
        AddTask("normal", "main", "Normal", DAILY, parent_id="ads", target=5),
        AddTask("tree", "main", "Tree", DAILY, target=45),
    )


def p(state, task_id, now=FRI):
    return progress(find_task(state, task_id), now)


def test_progress_counts_up_and_completes_at_target():
    s = run(ads(), SetProgress("normal", 3))
    assert p(s, "normal") == 3 and not is_done(find_task(s, "normal"), FRI)
    s = run(s, SetProgress("normal", 5))
    assert p(s, "normal") == 5 and is_done(find_task(s, "normal"), FRI)


def test_lowering_progress_uncompletes():
    s = run(ads(), SetProgress("tree", 45), SetProgress("tree", 44))
    assert p(s, "tree") == 44 and not is_done(find_task(s, "tree"), FRI)


def test_progress_is_clamped():
    assert p(run(ads(), SetProgress("tree", 999)), "tree") == 45


def test_progress_resets_daily():
    s = run(ads(), SetProgress("tree", 12), now=at(25, 10))
    assert p(s, "tree", at(25, 23)) == 12
    assert p(s, "tree", at(26, 1)) == 0
    s = run(ads(), SetProgress("tree", 45), now=at(25, 10))
    assert p(s, "tree", at(26, 1)) == 0


def test_checkbox_and_parent_fill_or_clear_counters():
    s = run(ads(), SetProgress("normal", 2), Complete("ads"))
    assert p(s, "normal") == 5 and item_done(s, find_task(s, "ads"), FRI)
    s = run(s, Uncomplete("ads"))
    assert p(s, "normal") == 0


def test_plain_tasks_have_no_counter():
    with pytest.raises(CommandError):
        run(ads(), SetProgress("gacha", 1))


def test_edit_target_and_bounds():
    s = run(ads(), EditTask("gacha", target=3), SetProgress("gacha", 2))
    assert p(s, "gacha") == 2
    with pytest.raises(CommandError):
        run(ads(), EditTask("gacha", target=-1))


def test_view_and_codec():
    s = run(ads(), SetProgress("normal", 2), SetProgress("tree", 7))
    tasks = {t["id"]: t for t in build_view(s, FRI)["bays"][0]["tasks"]}
    normal = next(c for c in tasks["ads"]["subtasks"] if c["id"] == "normal")
    assert (normal["counter"], normal["progress"], normal["target"]) == ("dots", 2, 5)
    assert (tasks["tree"]["counter"], tasks["tree"]["progress"]) == ("slider", 7)
    assert counter_style(0) is None and counter_style(10) == "dots" and counter_style(11) == "slider"
    assert state_from_dict(state_to_dict(s)) == s
    assert parse_command({"type": "set_progress", "task_id": "tree", "value": 7}, "_") == SetProgress("tree", 7)
    assert parse_command({"type": "add_task", "bay_id": "b", "title": "x", "target": 5}, "id").target == 5
