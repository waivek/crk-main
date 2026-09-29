from dataclasses import dataclass, replace
from datetime import date, datetime
from typing import Literal

RecurrenceKind = Literal["once", "daily", "weekly", "days_of_week", "interval", "windows"]
RECURRENCE_KINDS: tuple[RecurrenceKind, ...] = ("once", "daily", "weekly", "days_of_week", "interval", "windows")

DAY_MINUTES = 24 * 60
Window = tuple[int, int]  # (start, end) in minutes after the daily reset: 0 <= start < end <= DAY_MINUTES


@dataclass(frozen=True)
class Recurrence:
    kind: RecurrenceKind = "once"
    weekday: int = 0                    # weekly: the day the task resets (0 = Monday)
    days: frozenset[int] = frozenset()  # days_of_week / weekly: game days it's active (weekly: empty = all)
    every: int = 0                      # interval: resets every this many days...
    anchor: date | None = None          # interval: ...counting from this game day (one of its resets)
    windows: tuple[Window, ...] = ()    # windows: open during these, every day; claimable once per window


@dataclass(frozen=True)
class Timer:
    started_at: datetime
    seconds: int


@dataclass(frozen=True)
class Task:
    id: str
    bay_id: str
    title: str
    position: int
    enabled: bool = True
    recurrence: Recurrence = Recurrence()
    completed_at: datetime | None = None
    timer: Timer | None = None
    reminder_at: datetime | None = None
    categories: tuple[str, ...] = ()
    parent_id: str | None = None  # subtask of this (top-level) task, in the same bay
    target: int = 0               # >= 2: a counter (e.g. 5 ads), done when progress reaches it
    progress: int = 0
    progress_at: datetime | None = None
    origin_id: str | None = None     # the preset task this was copied from


@dataclass(frozen=True)
class Bay:
    id: str
    name: str
    position: int
    origin_id: str | None = None  # the preset bay this was copied from


@dataclass(frozen=True)
class Cooldown:
    """Something that refills over time and is emptied by claiming it: 1 more every `minutes`,
    up to `capacity`. A plain cooldown (the Fountain: claim, then wait 8 hours) is capacity 1.
    Not tied to the daily reset, so cooldowns live on their own tab rather than in a bay.
    It can be claimed at any time; that empties it and refilling starts over."""
    id: str
    title: str
    position: int
    minutes: int                        # time to refill one
    capacity: int = 1                   # refills up to this many
    emptied_at: datetime | None = None  # when it last held 0 (claimed, or set lower); None = full
    origin_id: str | None = None        # the preset cooldown this was copied from


@dataclass(frozen=True)
class Countdown:
    """A one-off timer (a "Timer" in the UI): something in the game finishes or expires at `ends_at`,
    e.g. a venture or a Mining Bell. Once it's over it stays, ringing, until it's dismissed."""
    id: str
    title: str
    ends_at: datetime
    seconds: int  # the length it was last set to: for the progress bar, and Restart


@dataclass(frozen=True)
class State:
    bays: tuple[Bay, ...] = ()
    tasks: tuple[Task, ...] = ()
    dismissed: frozenset[str] = frozenset()  # preset item ids the user hid from "Suggested"
    cooldowns: tuple[Cooldown, ...] = ()
    countdowns: tuple[Countdown, ...] = ()


MAX_TARGET = 1000
MAX_COOLDOWN_MINUTES = 30 * 24 * 60
MAX_CAPACITY = 1000
MAX_TIMER_SECONDS = 7 * 24 * 3600


def is_counter(task: "Task") -> bool:
    return task.target >= 2


class CommandError(ValueError):
    """A command could not be applied to the given state."""


def find_task(state: State, task_id: str) -> Task:
    for t in state.tasks:
        if t.id == task_id:
            return t
    raise CommandError(f"unknown task {task_id!r}")


def find_bay(state: State, bay_id: str) -> Bay:
    for b in state.bays:
        if b.id == bay_id:
            return b
    raise CommandError(f"unknown bay {bay_id!r}")


def find_cooldown(state: State, cooldown_id: str) -> Cooldown:
    for c in state.cooldowns:
        if c.id == cooldown_id:
            return c
    raise CommandError(f"unknown cooldown {cooldown_id!r}")


def find_countdown(state: State, countdown_id: str) -> Countdown:
    for c in state.countdowns:
        if c.id == countdown_id:
            return c
    raise CommandError(f"unknown timer {countdown_id!r}")


def cooldowns_in_order(state: State) -> list[Cooldown]:
    return sorted(state.cooldowns, key=lambda c: c.position)


def bays_in_order(state: State) -> list[Bay]:
    return sorted(state.bays, key=lambda b: b.position)


def tasks_in_bay(state: State, bay_id: str, parent_id: str | None = None) -> list[Task]:
    """Siblings in stored order: the bay's top-level tasks, or the subtasks of `parent_id`."""
    return sorted(
        (t for t in state.tasks if t.bay_id == bay_id and t.parent_id == parent_id),
        key=lambda t: t.position,
    )


def subtasks(state: State, task: Task) -> list[Task]:
    return tasks_in_bay(state, task.bay_id, task.id)


def update_tasks(state: State, updated: dict[str, Task]) -> State:
    """Replace tasks by id with the given versions."""
    return replace(state, tasks=tuple(updated.get(t.id, t) for t in state.tasks))
