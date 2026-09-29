from dataclasses import replace

from tools.todo.core import config
from tools.todo.core.model import Recurrence, State, Task, Timer
from tools.todo.core.schedule import (
    daily_reset_at_or_before,
    due_reminders,
    is_active,
    is_done,
    resets_at,
    timer_remaining,
)

from .helpers import at


def task(kind="daily", completed=None, **rec) -> Task:
    return Task("t", "b", "T", 0, recurrence=Recurrence(kind, **rec), completed_at=completed)


def test_daily_reset_boundary():
    assert daily_reset_at_or_before(at(25, 12)) == at(25, 0)
    assert daily_reset_at_or_before(at(25, 0)) == at(25, 0)


def kst(monkeypatch):
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(config, "RESET_TZ", ZoneInfo("Asia/Seoul"))


def test_kst_midnight_is_1500_utc(monkeypatch):
    kst(monkeypatch)
    assert daily_reset_at_or_before(at(25, 14, 59)) == at(24, 15)
    assert daily_reset_at_or_before(at(25, 15)) == at(25, 15)


def test_kst_game_days(monkeypatch):
    kst(monkeypatch)
    fri_sun = task("days_of_week", days=frozenset({4, 5, 6}))
    assert not is_active(fri_sun, at(24, 14, 59))  # Thu 23:59 KST
    assert is_active(fri_sun, at(24, 15))          # Fri 00:00 KST (Thu 15:00 UTC)
    assert is_active(fri_sun, at(27, 14, 59))      # Sun 23:59 KST
    assert not is_active(fri_sun, at(27, 15))      # Mon 00:00 KST


def test_kst_daily_and_weekly_resets(monkeypatch):
    kst(monkeypatch)
    daily = task("daily", completed=at(25, 14))
    assert is_done(daily, at(25, 14, 59))
    assert not is_done(daily, at(25, 15))
    weekly = task("weekly", completed=at(25), weekday=0)
    assert resets_at(weekly, at(25)) == at(27, 15)  # Mon 00:00 KST


def test_daily_done_until_next_reset():
    t = task("daily", completed=at(25, 10))
    assert is_done(t, at(25, 23, 59))
    assert not is_done(t, at(26, 0))
    assert resets_at(t, at(25, 12)) == at(26, 0)


def test_not_done_without_completion():
    assert not is_done(task("daily"), at(25))
    assert resets_at(task("daily"), at(25)) is None


def test_once_is_done_forever():
    t = task("once", completed=at(1))
    assert is_done(t, at(30))
    assert resets_at(t, at(30)) is None


def test_weekly_resets_on_its_weekday():
    # 2026-09-21 is a Monday
    t = task("weekly", completed=at(22), weekday=0)
    assert is_done(t, at(27, 23))
    assert not is_done(t, at(28, 0))
    assert resets_at(t, at(25)) == at(28, 0)


def test_weekly_completion_before_boundary_does_not_count():
    t = task("weekly", completed=at(20), weekday=0)  # Sunday, before Monday reset
    assert not is_done(t, at(21, 1))


def test_days_of_week_active_only_on_those_days():
    t = task("days_of_week", days=frozenset({4, 5, 6}))  # Fri, Sat, Sun
    assert is_active(t, at(25))      # Fri
    assert is_active(t, at(27))      # Sun
    assert not is_active(t, at(28))  # Mon


def test_days_of_week_resets_daily():
    t = task("days_of_week", completed=at(25, 9), days=frozenset({4, 5}))
    assert is_done(t, at(25, 20))
    assert not is_done(t, at(26, 1))


def test_timer_remaining_never_negative():
    timer = Timer(at(25, 12), 600)
    assert timer_remaining(timer, at(25, 12, 5)) == 300
    assert timer_remaining(timer, at(25, 13)) == 0


def test_due_reminders():
    base = Task("a", "b", "A", 0, recurrence=Recurrence("daily"), reminder_at=at(25, 10))
    state = State(tasks=(
        base,
        replace(base, id="later", reminder_at=at(25, 18)),
        replace(base, id="done", completed_at=at(25, 11)),
        replace(base, id="off", enabled=False),
        replace(base, id="none", reminder_at=None),
    ))
    assert [r.task_id for r in due_reminders(state, at(25, 12))] == ["a"]


def test_every_n_days_resets_on_its_cycle():
    from datetime import date
    # anchor Fri 2026-09-25: resets on the 25th, 28th, 1st, ...
    t = task("interval", completed=at(25, 9), every=3, anchor=date(2026, 9, 25))
    assert is_done(t, at(27, 23, 59))
    assert not is_done(t, at(28, 0))
    assert resets_at(t, at(26)) == at(28, 0)
    # anchors in the future work too (cycles count backwards)
    t = task("interval", completed=at(22, 1), every=3, anchor=date(2026, 10, 1))
    assert resets_at(t, at(23)) == at(25, 0)


def test_every_n_days_in_kst(monkeypatch):
    from datetime import date
    kst(monkeypatch)
    t = task("interval", completed=at(24, 16), every=3, anchor=date(2026, 9, 25))  # Fri 01:00 KST
    assert resets_at(t, at(25)) == at(27, 15)  # Mon 00:00 KST


def test_next_reset_previews_each_repeat():
    from datetime import date

    from tools.todo.core.schedule import next_reset
    now = at(25, 12)  # Fri
    assert next_reset(Recurrence("once"), now) is None
    assert next_reset(Recurrence("daily"), now) == at(26, 0)
    assert next_reset(Recurrence("weekly", weekday=0), now) == at(28, 0)
    assert next_reset(Recurrence("interval", every=3, anchor=date(2026, 9, 24)), now) == at(27, 0)
    assert next_reset(Recurrence("interval", every=3, anchor=date(2026, 9, 25)), now) == at(28, 0)


def test_weekly_with_a_tally_day():
    # resets Monday, active Mon–Sat; Sunday is the tally day
    t = task("weekly", weekday=0, days=frozenset(range(6)))
    assert is_active(t, at(26))       # Sat
    assert not is_active(t, at(27))   # Sun: tally
    assert is_active(t, at(28))       # Mon
    done = task("weekly", completed=at(22), weekday=0, days=frozenset(range(6)))
    assert is_done(done, at(27))      # stays done through the tally day...
    assert not is_done(done, at(28))  # ...and resets on Monday
    assert is_active(task("weekly", weekday=0), at(27))  # no active days = always active
