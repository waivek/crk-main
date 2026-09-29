"""Pure view model: what the UI should show, as plain JSON-able dicts."""

from datetime import datetime
from typing import Any

from . import config, preset as preset_rules
from .codec import dt_to_str, recurrence_to_dict
from .model import (
    DAY_MINUTES, Cooldown, Recurrence, State, Task, Window, bays_in_order, cooldowns_in_order, subtasks, tasks_in_bay,
)
from .schedule import (
    DAY,
    current_window,
    daily_reset_at_or_before,
    enabled_subtasks,
    full_at,
    is_active,
    is_done,
    is_full,
    is_reminder_due,
    item_active,
    item_done,
    level,
    next_refill_at,
    next_window_opens,
    progress,
    resets_at,
    timer_ends_at,
    timer_remaining,
)

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def day_range(days: frozenset[int]) -> str:
    """"Mon–Sat" for a run of 3+ consecutive days (wrapping past Sunday), else "Mon, Wed"."""
    if len(days) >= 3:
        for start in days:
            if (start - 1) % 7 not in days:
                run = 0
                while (start + run) % 7 in days:
                    run += 1
                if run == len(days):
                    return f"{WEEKDAYS[start]}–{WEEKDAYS[(start + run - 1) % 7]}"
    return ", ".join(WEEKDAYS[d] for d in sorted(days))


def recurrence_label(r: Recurrence) -> str:
    match r.kind:
        case "daily":
            return "Daily"
        case "weekly":
            label = f"Weekly · resets {WEEKDAYS[r.weekday]}"
            return label + (f" · active {day_range(r.days)}" if r.days else "")
        case "days_of_week":
            return ", ".join(WEEKDAYS[d] for d in sorted(r.days))
        case "interval":
            return f"Every {r.every} days"
        case "windows":
            if len(r.windows) <= 2:
                return ", ".join(window_label(w) for w in r.windows)
            return f"{len(r.windows)} windows a day"
        case _:
            return "Once"


def clock(minutes: int) -> str:
    """Minutes after the daily reset as a clock time in the reset timezone: 90 -> "01:30".
    The end of the day reads "24:00" rather than "00:00"."""
    reset = config.DAILY_RESET.hour * 60 + config.DAILY_RESET.minute
    total = (reset + minutes) % DAY_MINUTES
    if total == 0 and minutes == DAY_MINUTES:
        return "24:00"
    return f"{total // 60:02d}:{total % 60:02d}"


def window_label(w: Window) -> str:
    return f"{clock(w[0])}–{clock(w[1])}"


def window_view(task: Task, now: datetime) -> dict[str, Any] | None:
    """For time-window tasks: whether a window is open, and when it closes / the next one opens."""
    r = task.recurrence
    if r.kind != "windows":
        return None
    open_now = current_window(r.windows, now)
    return {
        "open": open_now is not None,
        "closes_at": dt_to_str(open_now[1]) if open_now else None,
        "opens_at": None if open_now else dt_to_str(next_window_opens(r.windows, now)),
        "times": [window_label(w) for w in r.windows],
    }


def duration_label(minutes: int) -> str:
    """"8h", "2h 30m", "45m"."""
    h, m = divmod(minutes, 60)
    return " ".join(p for p in (f"{h}h" if h else "", f"{m}m" if m else "") if p)


def counter_style(target: int) -> str | None:
    """How the UI should show a counter: a row of dots for small counts, else a slider."""
    if target < 2:
        return None
    return "dots" if target <= 10 else "slider"


def sort_key(state: State, task: Task, now: datetime) -> tuple[int, int]:
    """Pending first, then not-active-today, then completed; stored order within each group."""
    if item_done(state, task, now):
        rank = 2
    elif not item_active(state, task, now):
        rank = 1
    else:
        rank = 0
    return (rank, task.position)


def sorted_items(state: State, tasks: list[Task], now: datetime) -> list[Task]:
    """Opted-in tasks in display order, followed by opted-out ones."""
    on = sorted((t for t in tasks if t.enabled), key=lambda t: sort_key(state, t, now))
    return on + [t for t in tasks if not t.enabled]


def task_view(state: State, task: Task, now: datetime) -> dict[str, Any]:
    timer = None
    if task.timer is not None:
        timer = {
            "ends_at": dt_to_str(timer_ends_at(task.timer)),
            "seconds": task.timer.seconds,
            "remaining_seconds": timer_remaining(task.timer, now),
        }
    children = subtasks(state, task)
    counted = [c for c in enabled_subtasks(state, task) if is_active(c, now)]
    return {
        "id": task.id,
        "bay_id": task.bay_id,
        "parent_id": task.parent_id,
        "title": task.title,
        "position": task.position,
        "siblings": len(tasks_in_bay(state, task.bay_id, task.parent_id)),
        "enabled": task.enabled,
        "done": item_done(state, task, now),
        "active": item_active(state, task, now),
        "recurrence": recurrence_to_dict(task.recurrence),
        "recurrence_label": recurrence_label(task.recurrence),
        "resets_at": None if children else dt_to_str(resets_at(task, now)),
        "window": None if children else window_view(task, now),
        # Why an inactive item can't be ticked: between its time windows, or not one of its days.
        "inactive_label": "Closed" if any(
            t.recurrence.kind == "windows" for t in (enabled_subtasks(state, task) or [task])) else "Not today",
        "categories": list(task.categories),
        "timer": timer,
        "reminder_at": dt_to_str(task.reminder_at),
        "reminder_due": is_reminder_due(task, now),
        "is_group": bool(children),  # even when a filter shows none of its steps
        "subtasks": [task_view(state, c, now) for c in sorted_items(state, children, now)],
        "subtask_progress": {"done": sum(is_done(c, now) for c in counted), "total": len(counted)},
        # A finished group starts collapsed; the UI lets the user open it again.
        "collapsed": bool(children) and item_done(state, task, now),
        "target": task.target,
        "progress": progress(task, now),
        "counter": counter_style(task.target),
        "origin_id": task.origin_id,
    }


def cooldown_label(c: Cooldown) -> str:
    """"Every 8h" for a plain cooldown, "+1 every 2h · max 5" for one that refills."""
    every = duration_label(c.minutes)
    return f"Every {every}" if c.capacity == 1 else f"+1 every {every} · max {c.capacity}"


def cooldown_view(state: State, cooldown: Cooldown, now: datetime) -> dict[str, Any]:
    return {
        "id": cooldown.id,
        "title": cooldown.title,
        "position": cooldown.position,
        "siblings": len(state.cooldowns),
        "minutes": cooldown.minutes,
        "capacity": cooldown.capacity,
        "label": cooldown_label(cooldown),
        "level": level(cooldown, now),
        "full": is_full(cooldown, now),
        "next_at": dt_to_str(next_refill_at(cooldown, now)),
        "full_at": dt_to_str(full_at(cooldown, now)),
        "origin_id": cooldown.origin_id,
    }


def sorted_cooldowns(state: State, now: datetime) -> list[Cooldown]:
    """Soonest ready first: full ones (ready now) at the top, then by when each will be full.
    Stored order breaks ties (e.g. among the full ones)."""
    return sorted(cooldowns_in_order(state), key=lambda c: full_at(c, now) or now)


def build_view(state: State, now: datetime, *, can_undo: bool = False, can_redo: bool = False) -> dict[str, Any]:
    bays = []
    for bay in bays_in_order(state):
        tasks = tasks_in_bay(state, bay.id)
        enabled = [t for t in sorted_items(state, tasks, now) if t.enabled]
        counted = [t for t in enabled if item_active(state, t, now)]
        bays.append({
            "id": bay.id,
            "name": bay.name,
            "position": bay.position,
            "tasks": [task_view(state, t, now) for t in enabled],
            "opted_out": [task_view(state, t, now) for t in tasks if not t.enabled],
            "progress": {"done": sum(item_done(state, t, now) for t in counted), "total": len(counted)},
        })
    next_reset = daily_reset_at_or_before(now) + DAY
    return {
        "now": dt_to_str(now),
        "next_reset_at": dt_to_str(next_reset),
        "reset_label": f"{config.DAILY_RESET:%H:%M} {next_reset.astimezone(config.RESET_TZ).tzname()}",
        "reset_tz": next_reset.astimezone(config.RESET_TZ).tzname(),
        "bays": bays,
        "bay_count": len(bays),
        "categories": sorted({c for t in state.tasks for c in t.categories}, key=str.lower),
        "cooldowns": [cooldown_view(state, c, now) for c in sorted_cooldowns(state, now)],
        "cooldowns_full": sum(is_full(c, now) for c in state.cooldowns),
        "can_undo": can_undo,
        "can_redo": can_redo,
    }


def missing_view(user: State, preset: State) -> list[dict[str, Any]]:
    """The Browse list: preset items the user doesn't have, with what they'd look like."""
    tasks = {t.id: t for t in preset.tasks}
    cooldowns = {c.id: c for c in preset.cooldowns}
    return [
        {
            "id": m.id,
            "kind": m.kind,
            "title": m.title,
            "bay_name": m.bay_name,
            "group": m.group,
            "steps": m.steps,
            "hidden": m.hidden,
            "label": cooldown_label(cooldowns[m.id]) if m.kind == "cooldown" else (
                "Group" if m.steps else recurrence_label(tasks[m.id].recurrence)),
        }
        for m in preset_rules.missing(user, preset)
    ]


def filter_view(view: dict[str, Any], category: str | None, hide_done: bool = False) -> dict[str, Any]:
    """Narrow a built view to one #tag and/or to what's still to do. Progress counts, the tag list
    and cooldowns are left as they were."""
    view = _only_category(view, category) if category else view
    return _hide_done(view) if hide_done else view


def _hide_done(view: dict[str, Any]) -> dict[str, Any]:
    """Drop finished tasks, finished steps of unfinished groups, and groups left with no steps to
    show (a #tag filter can narrow a group to steps that are all done). Unlike a #tag filter, every
    bay stays (so it can say "all done"); `hidden_done` counts the rows it no longer shows."""
    def keep(tasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        out, hidden = [], 0
        for t in tasks:
            if t["done"]:
                hidden += 1 + len(t["subtasks"])
                continue
            steps, hidden_steps = keep(t["subtasks"])
            hidden += hidden_steps
            if t["subtasks"] and not steps:
                continue  # a group (maybe narrowed by a #tag) with none of its shown steps left to do
            out.append(t | {"subtasks": steps} if hidden_steps else t)
        return out, hidden

    bays = []
    for b in view["bays"]:
        tasks, hidden = keep(b["tasks"])
        opted_out, hidden_off = keep(b["opted_out"])
        bays.append(b | {"tasks": tasks, "opted_out": opted_out, "hidden_done": hidden + hidden_off})
    return view | {"bays": bays, "hide_done": True}


def _only_category(view: dict[str, Any], category: str) -> dict[str, Any]:
    """A task matching the tag is shown with all its subtasks; a parent that only matches through
    some subtasks is shown with just those; bays left with nothing are dropped."""

    def keep(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for t in tasks:
            if category in t["categories"]:
                out.append(t)
            elif matching := keep(t["subtasks"]):
                out.append(t | {"subtasks": matching, "collapsed": False})  # show what matched
        return out

    bays = []
    for b in view["bays"]:
        narrowed = b | {"tasks": keep(b["tasks"]), "opted_out": keep(b["opted_out"])}
        if narrowed["tasks"] or narrowed["opted_out"]:
            bays.append(narrowed)
    return view | {"bays": bays, "filter": category}
