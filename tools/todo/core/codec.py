"""Plain-dict <-> model conversion, and validated parsing of untrusted command payloads."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import Any

from . import commands as c
from . import ordering
from .model import (
    DAY_MINUTES, MAX_CAPACITY, MAX_COOLDOWN_MINUTES, MAX_TARGET, MAX_TIMER_SECONDS, RECURRENCE_KINDS, Window, Bay, Cooldown,
    Countdown, Recurrence, State, Task, Timer,
)

MAX_TEXT = 200
MAX_CATEGORIES = 20
MAX_WINDOWS = 96  # e.g. a 5-minute window every 15 minutes


class CodecError(ValueError):
    pass


# --- datetimes -------------------------------------------------------------------

def dt_to_str(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(UTC).isoformat()


def dt_from_str(s: str | None) -> datetime | None:
    if s is None:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        raise CodecError(f"invalid datetime {s!r}")
    if dt.tzinfo is None:
        raise CodecError(f"datetime must include a timezone: {s!r}")
    return dt.astimezone(UTC)


# --- model <-> dict -------------------------------------------------------------

def recurrence_to_dict(r: Recurrence) -> dict[str, Any]:
    match r.kind:
        case "weekly":
            return {"kind": "weekly", "weekday": r.weekday} | ({"days": sorted(r.days)} if r.days else {})
        case "days_of_week":
            return {"kind": "days_of_week", "days": sorted(r.days)}
        case "interval":
            assert r.anchor is not None
            return {"kind": "interval", "every": r.every, "anchor": r.anchor.isoformat()}
        case "windows":
            return {"kind": "windows", "windows": [list(w) for w in r.windows]}
        case kind:
            return {"kind": kind}


def recurrence_from_dict(d: Any) -> Recurrence:
    if not isinstance(d, dict):
        raise CodecError("recurrence must be an object")
    kind = d.get("kind")
    if kind not in RECURRENCE_KINDS:
        raise CodecError(f"unknown recurrence kind {kind!r}")
    match kind:
        case "weekly":
            days = d.get("days", [])
            if not isinstance(days, list):
                raise CodecError("days must be a list")
            active = frozenset(_weekday(x, "days") for x in days)
            if len(active) == 7:
                active = frozenset()  # every day is the same as no restriction
            return Recurrence("weekly", weekday=_weekday(d.get("weekday"), "weekday"), days=active)
        case "days_of_week":
            days = d.get("days")
            if not isinstance(days, list) or not days:
                raise CodecError("days must be a non-empty list")
            return Recurrence("days_of_week", days=frozenset(_weekday(x, "days") for x in days))
        case "interval":
            try:
                anchor = date.fromisoformat(d.get("anchor"))
            except (TypeError, ValueError):
                raise CodecError("anchor must be a date like 2026-09-24")
            return Recurrence("interval", every=_int(d, "every", 2, 365), anchor=anchor)
        case "windows":
            return Recurrence("windows", windows=_windows(d.get("windows")))
        case _:
            return Recurrence(kind)


def _windows(v: Any) -> tuple[Window, ...]:
    """Daily time windows as [start, end] minutes after the daily reset. Sorted; they may touch
    (12:00–24:00 after 00:00–12:00) but not overlap, and none may run past the next reset."""
    if not isinstance(v, list) or not 1 <= len(v) <= MAX_WINDOWS:
        raise CodecError(f"windows must be a list of 1 to {MAX_WINDOWS} [start, end] pairs")
    windows = []
    for w in v:
        if not (isinstance(w, list) and len(w) == 2 and all(isinstance(x, int) and not isinstance(x, bool) for x in w)):
            raise CodecError("each window must be [start, end] in minutes")
        start, end = w
        if not 0 <= start < DAY_MINUTES or not 0 < end <= DAY_MINUTES:
            raise CodecError("window times must be within the day (00:00 to 24:00)")
        if end <= start:
            raise CodecError("a window must end after it starts (split one that crosses midnight in two)")
        windows.append((start, end))
    windows.sort()
    if any(prev_end > start for (_, prev_end), (start, _) in zip(windows, windows[1:])):
        raise CodecError("windows must not overlap")
    return tuple(windows)


def timer_to_dict(t: Timer | None) -> dict[str, Any] | None:
    return None if t is None else {"started_at": dt_to_str(t.started_at), "seconds": t.seconds}


def timer_from_dict(d: dict[str, Any] | None) -> Timer | None:
    if d is None:
        return None
    started_at = dt_from_str(d["started_at"])
    assert started_at is not None
    return Timer(started_at, int(d["seconds"]))


def task_to_dict(t: Task) -> dict[str, Any]:
    return {
        "id": t.id,
        "bay_id": t.bay_id,
        "title": t.title,
        "position": t.position,
        "enabled": t.enabled,
        "recurrence": recurrence_to_dict(t.recurrence),
        "completed_at": dt_to_str(t.completed_at),
        "timer": timer_to_dict(t.timer),
        "reminder_at": dt_to_str(t.reminder_at),
        "categories": list(t.categories),
        "parent_id": t.parent_id,
        "target": t.target,
        "progress": t.progress,
        "progress_at": dt_to_str(t.progress_at),
        "origin_id": t.origin_id,
    }


def task_from_dict(d: dict[str, Any]) -> Task:
    return Task(
        id=d["id"],
        bay_id=d["bay_id"],
        title=d["title"],
        position=d["position"],
        enabled=bool(d["enabled"]),
        recurrence=recurrence_from_dict(d["recurrence"]),
        completed_at=dt_from_str(d["completed_at"]),
        timer=timer_from_dict(d["timer"]),
        reminder_at=dt_from_str(d["reminder_at"]),
        categories=tuple(d["categories"]),
        # .get(): absent in snapshots saved before these fields existed
        parent_id=d.get("parent_id"),
        target=d.get("target", 0),
        progress=d.get("progress", 0),
        progress_at=dt_from_str(d.get("progress_at")),
        origin_id=d.get("origin_id"),
    )


def bay_to_dict(b: Bay) -> dict[str, Any]:
    return {"id": b.id, "name": b.name, "position": b.position, "origin_id": b.origin_id}


def bay_from_dict(d: dict[str, Any]) -> Bay:
    return Bay(d["id"], d["name"], d["position"], d.get("origin_id"))


def cooldown_to_dict(c: Cooldown) -> dict[str, Any]:
    return {
        "id": c.id,
        "title": c.title,
        "position": c.position,
        "minutes": c.minutes,
        "capacity": c.capacity,
        "emptied_at": dt_to_str(c.emptied_at),
        "origin_id": c.origin_id,
    }


def cooldown_from_dict(d: dict[str, Any]) -> Cooldown:
    return Cooldown(
        id=d["id"],
        title=d["title"],
        position=d["position"],
        minutes=d["minutes"],
        # Snapshots from before refills existed had "claimed_at" (and an "early" flag, now
        # always allowed): a claimed plain cooldown is one that was emptied then.
        capacity=d.get("capacity", 1),
        emptied_at=dt_from_str(d["emptied_at"] if "emptied_at" in d else d.get("claimed_at")),
        origin_id=d.get("origin_id"),
    )


def countdown_to_dict(c: Countdown) -> dict[str, Any]:
    return {"id": c.id, "title": c.title, "ends_at": dt_to_str(c.ends_at), "seconds": c.seconds}


def countdown_from_dict(d: dict[str, Any]) -> Countdown:
    ends_at = dt_from_str(d["ends_at"])
    assert ends_at is not None
    return Countdown(id=d["id"], title=d["title"], ends_at=ends_at, seconds=d["seconds"])


def state_to_dict(s: State) -> dict[str, Any]:
    return {
        "bays": [bay_to_dict(b) for b in s.bays],
        "tasks": [task_to_dict(t) for t in s.tasks],
        "dismissed": sorted(s.dismissed),
        "cooldowns": [cooldown_to_dict(x) for x in s.cooldowns],
        "countdowns": [countdown_to_dict(x) for x in s.countdowns],
    }


def state_from_dict(d: dict[str, Any]) -> State:
    legacy = [t for t in d["tasks"] if t["recurrence"].get("kind") == "cooldown"]
    state = State(
        bays=tuple(bay_from_dict(b) for b in d["bays"]),
        tasks=tuple(task_from_dict(t) for t in d["tasks"] if t["recurrence"].get("kind") != "cooldown"),
        dismissed=frozenset(d.get("dismissed", ())),
        cooldowns=tuple(cooldown_from_dict(x) for x in d.get("cooldowns", ())),
        countdowns=tuple(countdown_from_dict(x) for x in d.get("countdowns", ())),  # older snapshots: none
    )
    return _adopt_legacy_cooldowns(state, legacy) if legacy else state


def _adopt_legacy_cooldowns(state: State, legacy: list[dict[str, Any]]) -> State:
    """Before cooldowns had their own tab they were tasks with a "cooldown" repeat. Move those
    tasks (kept as plain dicts, since that repeat no longer parses) into `state.cooldowns`,
    keeping their ids so preset origins still match, and drop bays left empty by the move.
    A group's own repeat never counted, so a "cooldown" group just stays put as a group."""
    bay_pos = {b.id: b.position for b in state.bays}
    groups = {t.parent_id for t in state.tasks}
    stay = [t for t in legacy if t["id"] in groups]
    move = sorted((t for t in legacy if t["id"] not in groups), key=lambda t: (bay_pos.get(t["bay_id"], 0), t["position"]))
    kept = tuple(task_from_dict(t | {"recurrence": {"kind": "once"}}) for t in stay)
    start = ordering.next_position(list(state.cooldowns))
    moved = tuple(
        Cooldown(
            id=t["id"],
            title=t["title"],
            position=start + i,
            minutes=t["recurrence"]["minutes"],
            emptied_at=dt_from_str(t["completed_at"]),
            origin_id=t.get("origin_id"),
        )
        for i, t in enumerate(move)
    )
    state = replace(state, tasks=state.tasks + kept, cooldowns=state.cooldowns + moved)
    for bay_id, parent_id in {(t["bay_id"], t.get("parent_id")) for t in move}:
        state = ordering.renumber_siblings(state, bay_id, parent_id)
    emptied = {t["bay_id"] for t in move} - {t.bay_id for t in state.tasks}
    state = replace(state, bays=tuple(b for b in state.bays if b.id not in emptied))
    return ordering.compact_bays(state)


# --- untrusted command payloads --------------------------------------------------

def _str(d: dict, key: str) -> str:
    v = d.get(key)
    if not isinstance(v, str) or not v.strip():
        raise CodecError(f"{key} must be a non-empty string")
    if len(v) > MAX_TEXT:
        raise CodecError(f"{key} is too long")
    return v


def _int(d: dict, key: str, lo: int, hi: int) -> int:
    v = d.get(key)
    if not isinstance(v, int) or isinstance(v, bool) or not lo <= v <= hi:
        raise CodecError(f"{key} must be an integer in [{lo}, {hi}]")
    return v


def _bool(d: dict, key: str) -> bool:
    v = d.get(key)
    if not isinstance(v, bool):
        raise CodecError(f"{key} must be a boolean")
    return v


def _weekday(v: Any, key: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 6:
        raise CodecError(f"{key} must be weekday numbers 0 (Mon) to 6 (Sun)")
    return v


def _categories(d: dict, key: str) -> tuple[str, ...]:
    v = d.get(key)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise CodecError(f"{key} must be a list of strings")
    cleaned = []
    for x in v:
        x = x.strip()
        if x and x not in cleaned:
            if len(x) > MAX_TEXT:
                raise CodecError("category is too long")
            cleaned.append(x)
    if len(cleaned) > MAX_CATEGORIES:
        raise CodecError("too many categories")
    return tuple(cleaned)


def _optional(d: dict, key: str, parse: Callable[[dict, str], Any]) -> Any:
    return None if d.get(key) is None else parse(d, key)


def _recurrence(d: dict, key: str) -> Recurrence:
    return recurrence_from_dict(d.get(key))


def _dt(d: dict, key: str) -> datetime:
    v = d.get(key)
    if not isinstance(v, str):
        raise CodecError(f"{key} must be an ISO datetime string")
    dt = dt_from_str(v)
    assert dt is not None
    return dt


def _minutes(d: dict, key: str) -> int:
    return _int(d, key, 1, MAX_COOLDOWN_MINUTES)


def _capacity(d: dict, key: str) -> int:
    return _int(d, key, 1, MAX_CAPACITY)


def _progress(d: dict, key: str) -> c.RefillProgress:
    """{"elapsed": minutes} or {"remaining": minutes}. The core checks it against the refill time."""
    v = d.get(key)
    if not isinstance(v, dict) or len(v) != 1 or not {"elapsed", "remaining"} >= v.keys():
        raise CodecError(f'{key} must be {{"elapsed": minutes}} or {{"remaining": minutes}}')
    [(which, _)] = v.items()
    return c.RefillProgress(_int(v, which, 0, MAX_COOLDOWN_MINUTES), remaining=which == "remaining")


def _value(d: dict, key: str) -> int:
    return _int(d, key, 0, MAX_CAPACITY)  # must also be within the capacity; the core checks that


def _target(d: dict, key: str) -> int:
    return _int(d, key, 0, MAX_TARGET)


def _ids(d: dict, key: str) -> tuple[str, ...]:
    v = d.get(key)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise CodecError(f"{key} must be a list of ids")
    return tuple(v)


def parse_command(d: Any, fresh_id: str) -> c.Command:
    """Parse a JSON payload into a Command. `fresh_id` is used for anything newly created,
    so clients never choose ids."""
    if not isinstance(d, dict):
        raise CodecError("command must be an object")
    match d.get("type"):
        case "add_bay":
            return c.AddBay(fresh_id, _str(d, "name"))
        case "rename_bay":
            return c.RenameBay(_str(d, "bay_id"), _str(d, "name"))
        case "remove_bay":
            return c.RemoveBay(_str(d, "bay_id"))
        case "move_bay":
            return c.MoveBay(_str(d, "bay_id"), _int(d, "index", 0, 10_000))
        case "add_task":
            return c.AddTask(
                fresh_id,
                _str(d, "bay_id"),
                _str(d, "title"),
                _optional(d, "recurrence", _recurrence) or Recurrence(),
                _optional(d, "categories", _categories) or (),
                _optional(d, "parent_id", _str),
                _optional(d, "target", _target) or 0,
            )
        case "edit_task":
            return c.EditTask(
                _str(d, "task_id"),
                _optional(d, "title", _str),
                _optional(d, "recurrence", _recurrence),
                _optional(d, "categories", _categories),
                _optional(d, "target", _target),
            )
        case "set_progress":
            return c.SetProgress(_str(d, "task_id"), _int(d, "value", 0, MAX_TARGET))
        case "remove_task":
            return c.RemoveTask(_str(d, "task_id"))
        case "duplicate_task":
            return c.DuplicateTask(_str(d, "task_id"), fresh_id)
        case "complete":
            return c.Complete(_str(d, "task_id"))
        case "uncomplete":
            return c.Uncomplete(_str(d, "task_id"))
        case "move_task":
            return c.MoveTask(_str(d, "task_id"), _str(d, "bay_id"), _int(d, "index", 0, 10_000))
        case "move_to_top":
            return c.MoveToTop(_str(d, "task_id"))
        case "move_to_bottom":
            return c.MoveToBottom(_str(d, "task_id"))
        case "set_enabled":
            return c.SetEnabled(_str(d, "task_id"), _bool(d, "enabled"))
        case "start_timer":
            return c.StartTimer(_str(d, "task_id"), _int(d, "seconds", 1, MAX_TIMER_SECONDS))
        case "stop_timer":
            return c.StopTimer(_str(d, "task_id"))
        case "set_reminder":
            return c.SetReminder(_str(d, "task_id"), _dt(d, "at"))
        case "clear_reminder":
            return c.ClearReminder(_str(d, "task_id"))
        case "add_cooldown":
            return c.AddCooldown(
                fresh_id,
                _str(d, "title"),
                _minutes(d, "minutes"),
                _optional(d, "capacity", _capacity) or 1,
                _optional(d, "value", _value) or 0,
                _optional(d, "progress", _progress),
            )
        case "edit_cooldown":
            return c.EditCooldown(
                _str(d, "cooldown_id"),
                _optional(d, "title", _str),
                _optional(d, "minutes", _minutes),
                _optional(d, "capacity", _capacity),
                _optional(d, "value", _value),
                _optional(d, "progress", _progress),
            )
        case "remove_cooldown":
            return c.RemoveCooldown(_str(d, "cooldown_id"))
        case "move_cooldown":
            return c.MoveCooldown(_str(d, "cooldown_id"), _int(d, "index", 0, 10_000))
        case "claim":
            return c.Claim(_str(d, "cooldown_id"))
        case "unclaim":
            return c.Unclaim(_str(d, "cooldown_id"))
        case "add_countdown":
            return c.AddCountdown(fresh_id, _str(d, "title"), _int(d, "seconds", 1, MAX_TIMER_SECONDS))
        case "edit_countdown":
            return c.EditCountdown(
                _str(d, "countdown_id"),
                _optional(d, "title", _str),
                _optional(d, "seconds", lambda d, k: _int(d, k, 1, MAX_TIMER_SECONDS)),
            )
        case "restart_countdown":
            return c.RestartCountdown(_str(d, "countdown_id"))
        case "remove_countdown":
            return c.RemoveCountdown(_str(d, "countdown_id"))
        case other:
            raise CodecError(f"unknown command type {other!r}")
