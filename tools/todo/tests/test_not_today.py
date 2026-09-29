import pytest

from tools.todo.core.commands import AddTask, Complete, SetProgress, Uncomplete
from tools.todo.core.model import CommandError, Recurrence, find_task
from tools.todo.core.schedule import is_done, item_done

from .helpers import FRI, at, run, seeded

MON_ONLY = Recurrence("days_of_week", days=frozenset({0}))  # FRI is a Friday
DAILY = Recurrence("daily")


def test_not_today_task_cannot_be_checked():
    s = run(seeded(), AddTask("mon", "main", "Monday thing", MON_ONLY))
    with pytest.raises(CommandError, match="isn't available today"):
        run(s, Complete("mon"))
    assert is_done(find_task(run(s, Complete("mon"), now=at(28)), "mon"), at(28))  # Monday is fine


def test_not_today_counter_cannot_move():
    s = run(seeded(), AddTask("mon", "main", "Monday count", MON_ONLY, target=5))
    with pytest.raises(CommandError):
        run(s, SetProgress("mon", 2))


def test_unchecking_stays_allowed():
    s = run(seeded(), AddTask("wk", "main", "Weekly, Mon-Thu", Recurrence("weekly", weekday=0, days=frozenset({0, 1, 2, 3}))),
            Complete("wk"), now=at(24))  # Thu
    s = run(s, Uncomplete("wk"))  # Fri: not active, but can still be unticked
    assert find_task(s, "wk").completed_at is None


def test_parent_ticks_only_todays_steps_and_is_done_without_the_others():
    s = run(
        seeded("group"),
        AddTask("today", "main", "Today", DAILY, parent_id="group"),
        AddTask("mon", "main", "Monday only", MON_ONLY, parent_id="group"),
        Complete("group"),
    )
    assert is_done(find_task(s, "today"), FRI)
    assert find_task(s, "mon").completed_at is None
    assert item_done(s, find_task(s, "group"), FRI)
    assert not item_done(s, find_task(s, "group"), at(28))  # Monday: its step is due again


def test_parent_with_no_steps_today_cannot_be_checked():
    s = run(seeded("group"), AddTask("mon", "main", "Monday only", MON_ONLY, parent_id="group"))
    with pytest.raises(CommandError):
        run(s, Complete("group"))
