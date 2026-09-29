"""Browse the preset: everything the admin has that the user doesn't, imported by picking items."""

from itertools import count

import pytest

from tools.todo.core.commands import AddBay, AddCooldown, AddTask, EditTask, RemoveTask
from tools.todo.core.model import CommandError, Recurrence, State, subtasks, tasks_in_bay
from tools.todo.core.preset import hide, import_items, instantiate, missing, suggested
from tools.todo.core.view import missing_view

from .helpers import run

DAILY = Recurrence("daily")


def ids(prefix="u"):
    return (f"{prefix}{i}" for i in count())


def preset() -> State:
    return run(
        State(),
        AddBay("pmain", "Main"),
        AddTask("pshop", "pmain", "Shop", DAILY),
        AddTask("pts", "pmain", "Town Square", DAILY),
        AddTask("pmail", "pmain", "Mail Box", DAILY, parent_id="pts"),
    )


def grown(p: State) -> State:
    """The admin added things after the user signed up."""
    return run(
        p,
        AddTask("parcade", "pmain", "Arcade", Recurrence("windows", windows=((0, 240),))),
        AddTask("pcake", "pmain", "Cake Hound", DAILY, parent_id="pts"),  # a new step in an existing group
        AddBay("pev", "Events"),
        AddTask("pash", "pev", "Ashes", DAILY),
        AddTask("parena", "pev", "Arena", DAILY, parent_id="pash"),
        AddCooldown("pf", "Fountain", 480),
    )


def user() -> State:
    return instantiate(preset(), ids())


def missing_ids(u, p):
    return [(m.kind, m.id) for m in missing(u, p)]


def test_nothing_missing_right_after_signup():
    assert missing(user(), preset()) == []


def test_lists_tasks_new_steps_and_cooldowns_in_preset_order():
    assert missing_ids(user(), grown(preset())) == [
        ("step", "pcake"), ("task", "parcade"), ("task", "pash"), ("cooldown", "pf"),
    ]


def test_includes_hidden_ones_and_marks_them():
    u = hide(user(), "parcade")
    assert [m.id for m in suggested(u, grown(preset()))] == ["pash", "pf"]  # Suggested skips hidden
    arcade = next(m for m in missing(u, grown(preset())) if m.id == "parcade")
    assert arcade.hidden


def test_items_the_user_deleted_come_back():
    u = user()
    shop = next(t for t in u.tasks if t.origin_id == "pshop")
    assert missing_ids(run(u, RemoveTask(shop.id)), preset()) == [("task", "pshop")]


def test_import_several_at_once():
    p = grown(preset())
    u = import_items(hide(user(), "parcade"), p, ["parcade", "pcake", "pf", "pash"], ids("n"))
    assert missing(u, p) == []
    main = next(b for b in u.bays if b.origin_id == "pmain")
    assert [t.title for t in tasks_in_bay(u, main.id)] == ["Shop", "Town Square", "Arcade"]
    town = next(t for t in u.tasks if t.origin_id == "pts")
    assert [(c.title, c.position) for c in subtasks(u, town)] == [("Mail Box", 0), ("Cake Hound", 1)]
    events = next(b for b in u.bays if b.origin_id == "pev")  # created for the imported event
    [ash] = tasks_in_bay(u, events.id)
    assert [c.title for c in subtasks(u, ash)] == ["Arena"]
    assert [(c.title, c.origin_id) for c in u.cooldowns] == [("Fountain", "pf")]


def test_step_joins_the_users_group_even_if_renamed_or_moved():
    u = user()
    town = next(t for t in u.tasks if t.origin_id == "pts")
    u = run(u, AddBay("mine", "Mine"), EditTask(town.id, title="TS"))
    from tools.todo.core.commands import MoveTask
    u = run(u, MoveTask(town.id, "mine", 0))
    [m] = [m for m in missing(u, grown(preset())) if m.kind == "step"]
    assert m.group == "TS"
    u = import_items(u, grown(preset()), ["pcake"], ids("n"))
    cake = next(t for t in u.tasks if t.origin_id == "pcake")
    assert (cake.parent_id, cake.bay_id) == (town.id, "mine")


@pytest.mark.parametrize("picked", [[], ["nope"], ["pshop"]])
def test_rejects_nothing_unknown_or_already_had(picked):
    with pytest.raises(CommandError):
        import_items(user(), grown(preset()), picked, ids("n"))


def test_view_labels():
    rows = {r["id"]: r for r in missing_view(user(), grown(preset()))}
    assert rows["parcade"]["label"] == "00:00–04:00"
    assert (rows["pash"]["label"], rows["pash"]["steps"]) == ("Group", 1)
    assert (rows["pcake"]["group"], rows["pcake"]["bay_name"]) == ("Town Square", "Main")
    assert (rows["pf"]["label"], rows["pf"]["bay_name"]) == ("Every 8h", "Cooldowns")
