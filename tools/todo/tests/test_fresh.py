"""'Available again': a repeat longer than a day that came back at today's reset (a shop restocked)."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from tools.todo.core import config
from tools.todo.core.commands import AddTask, Complete, SetEnabled
from tools.todo.core.model import Recurrence, find_task
from tools.todo.core.schedule import is_fresh, item_fresh
from tools.todo.core.view import build_view, filter_view

from .helpers import at, run, seeded

EVERY_3 = Recurrence("interval", every=3, anchor=date(2026, 9, 25))  # resets Fri 25th, Mon 28th, …
WEEKLY_FRI = Recurrence("weekly", weekday=4)  # 2026-09-25 is a Friday


def shop(recurrence: Recurrence = EVERY_3, now: datetime = at(25)):
    return run(seeded(), AddTask("shop", "main", "Arena Shop", recurrence), now=now)


def test_interval_is_fresh_only_on_its_reset_day():
    s = shop()
    t = find_task(s, "shop")
    assert is_fresh(t, at(25, 0, 0))
    assert is_fresh(t, at(25, 23, 59))
    assert not is_fresh(t, at(26, 0, 0))  # a day later: still to do, but no longer news
    assert not is_fresh(t, at(27))
    assert is_fresh(t, at(28, 9))  # the next reset


def test_weekly_is_fresh_on_its_reset_day():
    t = find_task(shop(WEEKLY_FRI), "shop")
    assert is_fresh(t, at(25))
    assert not is_fresh(t, at(26))


def test_done_is_not_fresh():
    s = run(shop(), Complete("shop"), now=at(25, 8))
    assert not is_fresh(find_task(s, "shop"), at(25, 9))
    assert is_fresh(find_task(s, "shop"), at(28, 9))  # it came back again


def test_daily_and_one_day_repeats_are_never_fresh():
    for r in (Recurrence("daily"), Recurrence("interval", every=1, anchor=date(2026, 9, 25)),
              Recurrence("days_of_week", days=frozenset({4})), Recurrence("once")):
        assert not is_fresh(find_task(shop(r), "shop"), at(25)), r.kind


def test_weekly_whose_reset_day_is_not_active_is_not_fresh():
    # Guild Battle style: resets Friday, but Friday is a tally day.
    r = Recurrence("weekly", weekday=4, days=frozenset({0, 1, 2, 3, 5, 6}))
    t = find_task(shop(r), "shop")
    assert not is_fresh(t, at(25))
    assert not is_fresh(t, at(26))


def test_opted_out_is_not_fresh():
    s = run(shop(), SetEnabled("shop", False), now=at(25))
    assert not is_fresh(find_task(s, "shop"), at(25))


def test_group_is_fresh_when_a_step_is():
    s = run(seeded("shops"), AddTask("a", "main", "Arena", EVERY_3, parent_id="shops"),
            AddTask("d", "main", "Daily", Recurrence("daily"), parent_id="shops"), now=at(25))
    assert item_fresh(s, find_task(s, "shops"), at(25))
    assert not item_fresh(s, find_task(s, "shops"), at(26))
    s = run(s, SetEnabled("a", False), now=at(25))
    assert not item_fresh(s, find_task(s, "shops"), at(25))


def test_view_flags_fresh_tasks():
    s = run(shop(), AddTask("daily", "main", "Daily", Recurrence("daily")), now=at(25))
    rows = {t["id"]: t["fresh"] for t in build_view(s, at(25))["bays"][0]["tasks"]}
    assert rows == {"shop": True, "daily": False}
    assert not build_view(s, at(26))["bays"][0]["tasks"][0]["fresh"]


def test_hide_done_keeps_fresh_tasks():
    view = filter_view(build_view(shop(), at(25)), None, hide_done=True)
    assert [t["fresh"] for t in view["bays"][0]["tasks"]] == [True]


def test_fresh_lasts_until_the_kst_reset(monkeypatch):
    monkeypatch.setattr(config, "RESET_TZ", ZoneInfo("Asia/Seoul"))
    t = find_task(shop(now=at(25, 3)), "shop")  # 25th 00:00 KST is the 24th 15:00 UTC
    assert is_fresh(t, datetime(2026, 9, 24, 15, 0, tzinfo=UTC))
    assert is_fresh(t, datetime(2026, 9, 25, 14, 59, tzinfo=UTC))
    assert not is_fresh(t, datetime(2026, 9, 25, 15, 0, tzinfo=UTC))
