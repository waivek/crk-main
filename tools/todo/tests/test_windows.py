"""Time-window events: open at set times every day, claimable once per window
(Monster Menace 00:00–12:00 & 12:00–24:00, the Post Office 00:00–00:30, 02:00–02:30, …)."""

from datetime import time

import pytest

from tools.todo.core import config
from tools.todo.core.codec import CodecError, recurrence_from_dict, recurrence_to_dict, state_from_dict, state_to_dict
from tools.todo.core.commands import AddTask, Complete, SetProgress, Uncomplete
from tools.todo.core.model import CommandError, Recurrence, find_task
from tools.todo.core.schedule import is_active, is_done, next_reset, resets_at
from tools.todo.core.view import build_view, recurrence_label

from .helpers import at, run, seeded

H = 60
MONSTER_MENACE = Recurrence("windows", windows=((0, 12 * H), (12 * H, 24 * H)))
POST_OFFICE = Recurrence("windows", windows=tuple((h * H, h * H + 30) for h in range(0, 24, 2)))
ARCADE = Recurrence("windows", windows=((0, 4 * H), (8 * H, 13 * H), (16 * H, 20 * H)))  # lengths vary


def with_event(recurrence: Recurrence, target: int = 0):
    return run(seeded(), AddTask("e", "main", "Event", recurrence, target=target), now=at(25, 0))


# --- open / closed ------------------------------------------------------------------------

def test_always_open_when_windows_touch():
    s = with_event(MONSTER_MENACE)
    t = find_task(s, "e")
    assert all(is_active(t, at(25, h)) for h in range(24))


@pytest.mark.parametrize("hour, minute, open_", [
    (0, 0, True), (0, 29, True), (0, 30, False), (1, 59, False), (2, 0, True), (22, 15, True), (23, 0, False),
])
def test_closed_between_windows(hour, minute, open_):
    t = find_task(with_event(POST_OFFICE), "e")
    assert is_active(t, at(25, hour, minute)) is open_


# --- once per window ----------------------------------------------------------------------

def test_claim_lasts_until_the_next_window_opens():
    s = run(with_event(MONSTER_MENACE), Complete("e"), now=at(25, 9))
    t = find_task(s, "e")
    assert is_done(t, at(25, 11, 59))
    assert resets_at(t, at(25, 10)) == at(25, 12)
    assert not is_done(t, at(25, 12))  # the 12:00 window is a fresh one
    s = run(s, Complete("e"), now=at(25, 23))
    assert resets_at(find_task(s, "e"), at(25, 23)) == at(26, 0)  # wraps to tomorrow's first window


def test_stays_done_while_closed_then_resets_when_the_next_window_opens():
    s = run(with_event(POST_OFFICE), Complete("e"), now=at(25, 0, 10))
    t = find_task(s, "e")
    assert is_done(t, at(25, 1)) and not is_active(t, at(25, 1))
    assert not is_done(t, at(25, 2))


def test_cannot_claim_while_closed_but_can_untick():
    s = run(with_event(POST_OFFICE), Complete("e"), now=at(25, 0, 10))
    with pytest.raises(CommandError, match="between windows"):
        run(with_event(POST_OFFICE), Complete("e"), now=at(25, 1))
    assert not is_done(find_task(run(s, Uncomplete("e"), now=at(25, 1)), "e"), at(25, 1))


def test_missed_window_does_not_carry_over():
    s = with_event(ARCADE)
    s = run(s, Complete("e"), now=at(25, 9))  # 08:00–13:00 window; the 00:00 one was missed
    assert resets_at(find_task(s, "e"), at(25, 10)) == at(25, 16)


def test_counters_reset_each_window():
    s = run(with_event(ARCADE, target=3), SetProgress("e", 2), now=at(25, 1))
    view = lambda when: build_view(s, when)["bays"][0]["tasks"][0]
    assert view(at(25, 3))["progress"] == 2
    assert view(at(25, 9))["progress"] == 0


def test_windows_follow_the_reset_timezone(monkeypatch):
    """Windows are clock times in KST: 12:00 KST is 03:00 UTC."""
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(config, "RESET_TZ", ZoneInfo("Asia/Seoul"))
    monkeypatch.setattr(config, "DAILY_RESET", time(0, 0))
    s = run(with_event(MONSTER_MENACE), Complete("e"), now=at(25, 1))  # 10:00 KST
    assert resets_at(find_task(s, "e"), at(25, 1)) == at(25, 3)
    assert next_reset(MONSTER_MENACE, at(25, 4)) == at(25, 15)  # 13:00 KST -> 00:00 KST


# --- view ---------------------------------------------------------------------------------

def test_view_window_info():
    s = with_event(POST_OFFICE)
    closed = build_view(s, at(25, 1))["bays"][0]["tasks"][0]
    assert not closed["active"] and closed["window"]["open"] is False
    assert closed["window"]["opens_at"] == "2026-09-25T02:00:00+00:00" and closed["window"]["closes_at"] is None
    open_ = build_view(s, at(25, 2, 10))["bays"][0]["tasks"][0]
    assert open_["window"] == {
        "open": True, "closes_at": "2026-09-25T02:30:00+00:00", "opens_at": None,
        "times": [f"{h:02d}:00–{h:02d}:30" for h in range(0, 24, 2)],
    }
    assert build_view(seeded("a"), at(25))["bays"][0]["tasks"][0]["window"] is None


def test_labels():
    assert recurrence_label(MONSTER_MENACE) == "00:00–12:00, 12:00–24:00"
    assert recurrence_label(Recurrence("windows", windows=((8 * H, 12 * H),))) == "08:00–12:00"
    assert recurrence_label(POST_OFFICE) == "12 windows a day"


def test_text_view_says_closed():
    from tools.todo.core.text_view import render_text
    text = render_text(build_view(with_event(POST_OFFICE), at(25, 1)))
    assert "closed" in text and "opens in 1h 00m" in text


# --- codec --------------------------------------------------------------------------------

def test_round_trip_and_sorting():
    r = recurrence_from_dict({"kind": "windows", "windows": [[720, 1440], [0, 720]]})
    assert r == MONSTER_MENACE
    assert recurrence_from_dict(recurrence_to_dict(ARCADE)) == ARCADE
    s = with_event(POST_OFFICE)
    assert state_from_dict(state_to_dict(s)) == s


@pytest.mark.parametrize("windows", [
    [],                        # none
    [[0, 30], [20, 60]],       # overlap
    [[600, 540]],              # ends before it starts
    [[1380, 60]],              # crosses midnight
    [[0, 1441]],               # past the end of the day
    [[-5, 30]],
    [[0, 30, 60]],
    [["00:00", "00:30"]],
    [[0, True]],
])
def test_rejects_bad_windows(windows):
    with pytest.raises(CodecError):
        recurrence_from_dict({"kind": "windows", "windows": windows})
