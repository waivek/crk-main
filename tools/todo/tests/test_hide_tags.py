"""Hiding #tags: tasks carrying any hidden tag are left out of the view."""

from tools.todo.core.commands import AddTask, Complete, EditTask, SetEnabled
from tools.todo.core.model import Recurrence
from tools.todo.core.view import build_view, filter_view

from .helpers import FRI, run, seeded

DAILY = Recurrence("daily")


def ads_state():
    return run(
        seeded("guild", "bounties", "ads", "ts"),
        AddTask("gacha", "main", "Gacha", DAILY, parent_id="ads"),
        AddTask("normal", "main", "Normal", DAILY, parent_id="ads"),
        AddTask("mail", "main", "Mail", DAILY, parent_id="ts"),
        AddTask("cake", "main", "Cake", DAILY, parent_id="ts"),
        EditTask("guild", categories=("Weekly",)),
        EditTask("ads", categories=("Ads",)),
        EditTask("mail", categories=("Ads",)),
    )


def ids(view, bay=0):
    return {t["id"]: [c["id"] for c in t["subtasks"]] for t in view["bays"][bay]["tasks"]}


def test_hides_tagged_tasks_with_their_steps_and_tagged_steps():
    full = build_view(ads_state(), FRI)
    v = filter_view(full, None, hidden_tags=frozenset({"Ads"}))
    assert ids(v) == {"guild": [], "bounties": [], "ts": ["cake"]}
    assert v["bays"][0]["hidden_tagged"] == 3 + 1  # ads + its 2 steps, mail
    assert v["hidden_tags"] == ["Ads"]
    assert v["bays"][0]["progress"] == full["bays"][0]["progress"]  # counts still cover everything


def test_several_tags_and_bays_stay():
    s = run(ads_state(), EditTask("bounties", categories=("Weekly",)), EditTask("cake", categories=("Ads",)))
    v = filter_view(build_view(s, FRI), None, hidden_tags=frozenset({"Ads", "Weekly"}))
    assert ids(v) == {}  # the group lost every step, so it goes too
    assert v["bays"][0]["hidden_tagged"] == 3 + 3 + 2  # ads group, ts group + 2 steps, guild, bounties


def test_no_hidden_tags_leaves_the_view_alone():
    full = build_view(ads_state(), FRI)
    assert filter_view(full, None, hidden_tags=frozenset()) is full
    assert ids(filter_view(full, None, hidden_tags=frozenset({"nope"}))) == ids(full)


def test_hiding_wins_over_show_only():
    s = run(ads_state(), EditTask("bounties", categories=("Weekly", "Ads")))
    v = filter_view(build_view(s, FRI), "Weekly", hidden_tags=frozenset({"Ads"}))
    assert ids(v) == {"guild": []}


def test_combines_with_hide_done():
    s = run(ads_state(), Complete("cake"), Complete("bounties"))
    v = filter_view(build_view(s, FRI), None, hide_done=True, hidden_tags=frozenset({"Ads"}))
    assert ids(v) == {"guild": []}  # ts is left with only its done step: dropped by hide-done
    assert v["bays"][0]["hidden_tagged"] == 4 and v["bays"][0]["hidden_done"] == 2  # bounties, cake


def test_opted_out_tasks_are_hidden_too():
    s = run(ads_state(), SetEnabled("guild", False))
    v = filter_view(build_view(s, FRI), None, hidden_tags=frozenset({"Weekly"}))
    assert v["bays"][0]["opted_out"] == [] and v["bays"][0]["hidden_tagged"] == 1
