from itertools import count

import pytest

from tools.todo.core.commands import AddBay, AddTask, EditTask, RemoveTask, SetProgress
from tools.todo.core.model import CommandError, Recurrence, State, find_task, subtasks, tasks_in_bay
from tools.todo.core.preset import adopt_origins, hide, import_item, instantiate, suggested

from .helpers import FRI, run

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


def with_event(p: State) -> State:
    return run(
        p,
        AddBay("pev", "Events"),
        AddTask("pash", "pev", "Ashes of Ruin Missions", DAILY),
        AddTask("parena", "pev", "Play in Kingdom Arena 3 Times", DAILY, parent_id="pash", target=3),
        AddTask("pball", "pev", "Complete a Balloon Expedition 1 Time", DAILY, parent_id="pash"),
    )


def suggested_ids(user, p):
    return [s.id for s in suggested(user, p)]


def test_new_user_gets_a_full_copy_with_origins():
    p = with_event(preset())
    u = instantiate(p, ids())
    assert [(b.name, b.origin_id) for b in u.bays] == [("Main", "pmain"), ("Events", "pev")]
    ts = next(t for t in u.tasks if t.origin_id == "pts")
    assert [c.title for c in subtasks(u, ts)] == ["Mail Box"]
    assert not {t.id for t in u.tasks} & {t.id for t in p.tasks}  # fresh ids
    assert suggested_ids(u, p) == []


def test_new_preset_item_is_suggested_and_imported_with_steps():
    u = instantiate(preset(), ids())
    p = with_event(preset())
    [s] = suggested(u, p)
    assert (s.id, s.title, s.bay_name, s.steps) == ("pash", "Ashes of Ruin Missions", "Events", 2)
    u = import_item(u, p, "pash", ids("n"))
    assert [b.name for b in u.bays] == ["Main", "Events"]  # bay created on demand
    event = next(t for t in u.tasks if t.origin_id == "pash")
    assert [(c.title, c.target) for c in subtasks(u, event)] == [
        ("Play in Kingdom Arena 3 Times", 3), ("Complete a Balloon Expedition 1 Time", 0)]
    assert suggested_ids(u, p) == []


def test_import_into_existing_bay_appends():
    p = run(preset(), AddTask("pnew", "pmain", "Arena Shop", Recurrence("interval", every=3, anchor=FRI.date())))
    u = import_item(instantiate(preset(), ids()), p, "pnew", ids("n"))
    main = next(b for b in u.bays if b.origin_id == "pmain")
    assert [t.title for t in tasks_in_bay(u, main.id)] == ["Shop", "Town Square", "Arena Shop"]
    assert next(t for t in u.tasks if t.origin_id == "pnew").recurrence.every == 3


def test_hide_removes_from_suggested():
    u = hide(instantiate(preset(), ids()), "pash")
    assert suggested_ids(u, with_event(preset())) == []


def test_deleted_copy_is_suggested_again():
    p = preset()
    u = instantiate(p, ids())
    shop = next(t for t in u.tasks if t.origin_id == "pshop")
    assert suggested_ids(run(u, RemoveTask(shop.id)), p) == ["pshop"]


def test_preset_edits_do_not_touch_user_copies():
    p = preset()
    u = instantiate(p, ids())
    assert suggested_ids(u, run(p, EditTask("pshop", title="Renamed"))) == []
    assert suggested_ids(u, run(p, RemoveTask("pshop"))) == []


def test_importing_something_not_suggested_fails():
    with pytest.raises(CommandError):
        import_item(instantiate(preset(), ids()), preset(), "pshop", ids())


def test_imported_counters_work_like_any_other():
    u = import_item(instantiate(preset(), ids()), with_event(preset()), "pash", ids("n"))
    arena = next(t for t in u.tasks if t.origin_id == "parena")
    u = run(u, SetProgress(arena.id, 3))
    assert find_task(u, arena.id).completed_at == FRI


def test_adopt_origins_matches_by_title_path():
    p = preset()
    legacy = run(
        State(),
        AddBay("b", "Main"),
        AddTask("s", "b", "Shop", DAILY),
        AddTask("t", "b", "Town Square", DAILY),
        AddTask("m", "b", "Mail Box", DAILY, parent_id="t"),
        AddTask("x", "b", "My own thing", DAILY),
    )
    u = adopt_origins(legacy, p)
    assert {t.id: t.origin_id for t in u.tasks} == {"s": "pshop", "t": "pts", "m": "pmail", "x": None}
    assert u.bays[0].origin_id == "pmain"
    assert suggested_ids(u, p) == []
