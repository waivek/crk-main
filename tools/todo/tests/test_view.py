from tools.todo.core.auth_rules import normalize_username, signup_errors
from tools.todo.core.commands import AddCooldown, AddTask, Claim, Complete, EditTask, SetEnabled, StartTimer
from tools.todo.core.model import Recurrence, tasks_in_bay
from tools.todo.core.view import build_view, recurrence_label

from .helpers import FRI, at, run, seeded


def bay(view, i=0):
    return view["bays"][i]


def test_completed_sorted_to_bottom_keeping_stored_order():
    s = run(seeded("a", "b", "c"), Complete("a"))
    assert [t["title"] for t in bay(build_view(s, FRI))["tasks"]] == ["b", "c", "a"]


def test_inactive_day_tasks_sit_between_pending_and_done():
    s = run(
        seeded("a", "b"),
        AddTask("mon", "main", "mon", Recurrence("days_of_week", days=frozenset({0}))),
        Complete("a"),
    )
    v = bay(build_view(s, FRI))
    assert [t["title"] for t in v["tasks"]] == ["b", "mon", "a"]
    assert v["progress"] == {"done": 1, "total": 2}  # inactive task not counted


def test_opted_out_excluded_from_tasks_and_progress():
    s = run(seeded("a", "b"), SetEnabled("b", False))
    v = bay(build_view(s, FRI))
    assert [t["id"] for t in v["tasks"]] == ["a"]
    assert [t["id"] for t in v["opted_out"]] == ["b"]
    assert v["progress"] == {"done": 0, "total": 1}


def test_task_view_countdowns():
    s = run(
        seeded("a", "b"),
        EditTask("a", categories=("arena",)),
        Complete("a"),
        StartTimer("b", 600),
    )
    tasks = {t["id"]: t for t in bay(build_view(s, at(25, 12, 5)))["tasks"]}
    assert tasks["a"]["resets_at"] == "2026-09-26T00:00:00+00:00"
    assert tasks["b"]["timer"]["remaining_seconds"] == 300
    assert build_view(s, FRI)["categories"] == ["arena"]


def test_recurrence_labels():
    assert recurrence_label(Recurrence()) == "Once"
    assert recurrence_label(Recurrence("weekly", weekday=2)) == "Weekly · resets Wed"
    assert recurrence_label(Recurrence("days_of_week", days=frozenset({6, 4}))) == "Fri, Sun"


def test_signup_rules():
    assert signup_errors("vivek", "password1", "password1") == []
    assert normalize_username("  Vivek ") == "vivek"
    assert len(signup_errors("v", "short", "other")) == 3


def test_starter_template():
    from itertools import count

    from tools.todo.core.defaults import starter_state
    from tools.todo.core.model import subtasks

    s = starter_state(f"id{i}" for i in count())
    assert [b.name for b in s.bays] == ["Main"]
    top = tasks_in_bay(s, s.bays[0].id)
    layout = {t.title: [c.title for c in subtasks(s, t)] for t in top}
    assert list(layout) == [
        "Bounties", "Starlight Island", "Monster Menace", "Ads", "Town Square", "Tree of Wishes", "Shop",
    ]
    assert layout["Monster Menace"] == ["Red Chest", "Blue Chest", "Green Chest"]
    targets = {t.title: t.target for t in s.tasks if t.target}
    assert targets == {"Normal Ads": 5, "Tree of Wishes": 45}
    assert layout["Town Square"] == ["Mail Box", "Cake Hound", "Town Square Event"]
    assert layout["Ads"] == ["Gacha Ad", "Normal Ads"]
    assert len(layout["Starlight Island"]) == len(layout["Monster Menace"]) == 3
    daily = [t for t in s.tasks if t.bay_id == s.bays[0].id]
    assert {t.recurrence.kind for t in daily} == {"daily"}
    assert [(c.title, c.minutes, c.capacity) for c in s.cooldowns] == [("Fountain", 480, 1), ("Harbour Ship", 480, 1)]
    assert len({t.id for t in s.tasks}) == len(s.tasks) == 18


def test_subtasks_nested_in_view():
    s = run(
        seeded("ts"),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        AddTask("cake", "main", "Cake", Recurrence("daily"), parent_id="ts"),
        AddTask("other", "main", "Other"),
        Complete("mail"),
    )
    v = bay(build_view(s, FRI))
    assert [t["title"] for t in v["tasks"]] == ["ts", "Other"]  # subtasks are not top-level rows
    ts = v["tasks"][0]
    assert [c["title"] for c in ts["subtasks"]] == ["Cake", "Mail"]  # done subtask sorts last
    assert ts["subtask_progress"] == {"done": 1, "total": 2}
    assert not ts["done"]
    v = bay(build_view(run(s, Complete("ts")), FRI))
    assert v["tasks"][-1]["title"] == "ts" and v["tasks"][-1]["done"]
    assert v["progress"] == {"done": 1, "total": 2}


def test_view_has_next_reset():
    v = build_view(seeded("a"), at(25, 12))
    assert v["next_reset_at"] == "2026-09-26T00:00:00+00:00"
    assert v["reset_label"] == "00:00 UTC"


def test_signup_reserves_robot():
    assert "That username is reserved." in signup_errors("Robot", "password1", "password1")


def test_text_view_renders():
    from tools.todo.core.text_view import render_text

    s = run(seeded("a"), AddCooldown("f", "Fountain", 480), AddCooldown("t", "Tickets", 120, capacity=5, value=2))
    text = render_text(build_view(s, FRI))
    assert "Bay 1 · Daily" in text
    assert "[0/1] Fountain  (Every 8h; ready in 8h 00m)" in text
    assert "[2/5] Tickets  (+1 every 2h · max 5; +1 in 2h 00m, full in 6h 00m)" in text


def test_weekly_labels_and_day_ranges():
    from tools.todo.core.view import day_range
    assert recurrence_label(Recurrence("weekly", weekday=0)) == "Weekly · resets Mon"
    assert recurrence_label(Recurrence("weekly", weekday=0, days=frozenset(range(6)))) == "Weekly · resets Mon · active Mon–Sat"
    assert day_range(frozenset({5, 6, 0})) == "Sat–Mon"
    assert day_range(frozenset({0, 2})) == "Mon, Wed"


def test_filter_view_by_tag():
    from tools.todo.core.view import filter_view

    s = run(
        seeded("ads", "arena", "plain"),
        AddTask("gacha", "main", "Gacha", Recurrence("daily"), parent_id="ads"),
        AddTask("normal", "main", "Normal", Recurrence("daily"), parent_id="ads"),
        AddTask("ts", "main", "Town Square", Recurrence("daily")),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        EditTask("gacha", categories=("ad",)),
        EditTask("arena", categories=("pvp",)),
        EditTask("ts", categories=("pvp",)),
    )
    from tools.todo.core.commands import AddBay
    s = run(s, AddBay("empty", "Other"), AddTask("x", "empty", "Untagged"))
    full = build_view(s, FRI)
    assert filter_view(full, None) is full

    ad = filter_view(full, "ad")
    assert [b["name"] for b in ad["bays"]] == ["Daily"]          # bay without matches dropped
    [ads] = ad["bays"][0]["tasks"]
    assert [c["title"] for c in ads["subtasks"]] == ["Gacha"]     # sibling without the tag hidden
    assert ad["bay_count"] == 2 and ad["filter"] == "ad"

    pvp = filter_view(full, "pvp")
    titles = {t["title"]: [c["title"] for c in t["subtasks"]] for t in pvp["bays"][0]["tasks"]}
    assert titles == {"arena": [], "Town Square": ["Mail"]}      # a matching parent keeps its subtasks
    assert filter_view(full, "nope")["bays"] == []


def test_finished_groups_start_collapsed():
    s = run(
        seeded("group", "plain"),
        AddTask("a", "main", "A", Recurrence("daily"), parent_id="group"),
        AddTask("b", "main", "B", Recurrence("daily"), parent_id="group"),
        Complete("a"),
    )
    tasks = lambda st: {t["id"]: t for t in build_view(st, FRI)["bays"][0]["tasks"]}
    assert tasks(s)["group"]["collapsed"] is False        # one step left
    assert tasks(run(s, Complete("b")))["group"]["collapsed"] is True
    assert tasks(run(s, Complete("plain")))["plain"]["collapsed"] is False  # no steps, nothing to collapse


def test_hide_done():
    from tools.todo.core.view import filter_view

    s = run(
        seeded("a", "b", "ts", "fin", "off"),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        AddTask("cake", "main", "Cake", Recurrence("daily"), parent_id="ts"),
        AddTask("s1", "main", "S1", Recurrence("daily"), parent_id="fin"),
        Complete("b"), Complete("mail"), Complete("fin"),
        SetEnabled("off", False), Complete("off"),
    )
    full = build_view(s, FRI)
    v = filter_view(full, None, hide_done=True)
    b = v["bays"][0]
    assert [t["id"] for t in b["tasks"]] == ["a", "ts"]           # b and the finished group are gone
    assert [c["id"] for c in b["tasks"][1]["subtasks"]] == ["cake"]  # only the group's unfinished step
    assert b["opted_out"] == []
    assert b["hidden_done"] == 1 + 2 + 1 + 1  # b, fin + its step, mail, off
    assert b["progress"] == full["bays"][0]["progress"]  # counts still cover everything
    assert v["hide_done"] and filter_view(full, None) is full


def test_hide_done_keeps_bays_that_are_all_done():
    from tools.todo.core.view import filter_view

    v = filter_view(build_view(run(seeded("a"), Complete("a")), FRI), None, hide_done=True)
    assert v["bays"][0]["tasks"] == [] and v["bays"][0]["hidden_done"] == 1


def test_hide_done_combines_with_a_tag():
    from tools.todo.core.view import filter_view

    s = run(seeded("a", "b", "c"), EditTask("a", categories=("x",)), EditTask("b", categories=("x",)), Complete("a"))
    v = filter_view(build_view(s, FRI), "x", hide_done=True)
    assert [t["id"] for t in v["bays"][0]["tasks"]] == ["b"]


def test_hide_done_with_a_tag_drops_a_group_whose_matching_steps_are_all_done():
    """Bug: #tag keeps a group with just its tagged step; once that step is done, "Hide done" left
    the group with no steps, and it showed up as a plain task with a checkbox."""
    from tools.todo.core.view import filter_view

    s = run(
        seeded("ts", "other"),
        AddTask("mail", "main", "Mail", Recurrence("daily"), parent_id="ts"),
        AddTask("cake", "main", "Cake", Recurrence("daily"), parent_id="ts"),
        EditTask("mail", categories=("x",)),
        EditTask("other", categories=("x",)),
        Complete("mail"),
    )
    v = filter_view(build_view(s, FRI), "x", hide_done=True)
    assert [t["id"] for t in v["bays"][0]["tasks"]] == ["other"]
    assert v["bays"][0]["hidden_done"] == 1  # the done step (the group itself isn't done)
    group = next(t for t in filter_view(build_view(s, FRI), "x")["bays"][0]["tasks"] if t["id"] == "ts")
    assert group["is_group"] and not next(t for t in v["bays"][0]["tasks"])["is_group"]
