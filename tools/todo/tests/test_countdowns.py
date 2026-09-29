"""Standalone timers ("countdowns"): one-off, ending at a set time, on the Timers tab."""

from datetime import timedelta

import pytest

from tools.todo.core import codec
from tools.todo.core.commands import AddCountdown, EditCountdown, RemoveCountdown, RestartCountdown
from tools.todo.core.model import CommandError, State, find_countdown
from tools.todo.core.text_view import render_text
from tools.todo.core.view import build_view

from .helpers import FRI, run

H = 3600


def bell(now=FRI):
    return run(State(), AddCountdown("bell", "Mining Bell", 10 * H + 37 * 60), now=now)


def test_add_ends_that_long_from_now():
    c = find_countdown(bell(), "bell")
    assert c.title == "Mining Bell"
    assert c.ends_at == FRI + timedelta(hours=10, minutes=37)
    assert c.seconds == 10 * H + 37 * 60


@pytest.mark.parametrize("seconds", [0, -5, 7 * 24 * H + 1])
def test_length_must_be_sensible(seconds):
    with pytest.raises(CommandError):
        run(State(), AddCountdown("x", "X", seconds))


def test_title_is_required_and_trimmed():
    with pytest.raises(CommandError):
        run(State(), AddCountdown("x", "   ", 60))
    assert find_countdown(run(State(), AddCountdown("x", "  Venture ", 60)), "x").title == "Venture"


def test_ids_are_unique():
    with pytest.raises(CommandError):
        run(bell(), AddCountdown("bell", "Again", 60))


def test_edit_title_keeps_the_time():
    s = run(bell(), EditCountdown("bell", title="Bell"), now=FRI + timedelta(hours=1))
    c = find_countdown(s, "bell")
    assert c.title == "Bell" and c.ends_at == FRI + timedelta(hours=10, minutes=37)


def test_edit_time_left_counts_from_now():
    later = FRI + timedelta(hours=1)
    c = find_countdown(run(bell(), EditCountdown("bell", seconds=30 * 60), now=later), "bell")
    assert c.ends_at == later + timedelta(minutes=30) and c.seconds == 30 * 60


def test_restart_runs_the_same_length_again():
    later = FRI + timedelta(hours=12)
    c = find_countdown(run(bell(), RestartCountdown("bell"), now=later), "bell")
    assert c.ends_at == later + timedelta(hours=10, minutes=37)


def test_remove():
    assert run(bell(), RemoveCountdown("bell")).countdowns == ()
    with pytest.raises(CommandError):
        run(State(), RemoveCountdown("nope"))


def test_view_orders_by_end_and_flags_finished_ones():
    s = run(bell(), AddCountdown("venture", "Venture", 2 * H), AddCountdown("soon", "Soon", 60))
    at = FRI + timedelta(hours=3)
    v = build_view(s, at)
    assert [c["id"] for c in v["countdowns"]] == ["soon", "venture", "bell"]
    assert [c["over"] for c in v["countdowns"]] == [True, True, False]
    assert v["countdowns_over"] == 2
    assert v["countdowns"][2]["remaining_seconds"] == 7 * H + 37 * 60
    assert v["countdowns"][0]["remaining_seconds"] == 0
    assert "[OVER] Soon" in render_text(v)


def test_codec_round_trip_and_old_snapshots():
    s = bell()
    assert codec.state_from_dict(codec.state_to_dict(s)) == s
    old = codec.state_to_dict(s)
    del old["countdowns"]
    assert codec.state_from_dict(old).countdowns == ()


def test_parse_commands():
    assert codec.parse_command({"type": "add_countdown", "title": "Bell", "seconds": 90}, "new") == AddCountdown("new", "Bell", 90)
    assert codec.parse_command({"type": "edit_countdown", "countdown_id": "b", "seconds": 60}, "x") == EditCountdown("b", None, 60)
    assert codec.parse_command({"type": "restart_countdown", "countdown_id": "b"}, "x") == RestartCountdown("b")
    assert codec.parse_command({"type": "remove_countdown", "countdown_id": "b"}, "x") == RemoveCountdown("b")
    for bad in ({"type": "add_countdown", "title": "Bell"}, {"type": "add_countdown", "title": "", "seconds": 5},
                {"type": "add_countdown", "title": "Bell", "seconds": 0}, {"type": "edit_countdown", "seconds": 5}):
        with pytest.raises(codec.CodecError):
            codec.parse_command(bad, "x")
