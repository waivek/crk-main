"""Commands and the pure `apply` that turns (state, command, now) into a new state."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from . import ordering
from .model import (
    MAX_CAPACITY,
    MAX_COOLDOWN_MINUTES,
    MAX_TARGET,
    MAX_TIMER_SECONDS,
    Bay,
    CommandError,
    Cooldown,
    Countdown,
    Recurrence,
    State,
    Task,
    Timer,
    find_bay,
    find_cooldown,
    find_countdown,
    find_task,
    is_counter,
    subtasks,
    tasks_in_bay,
    update_tasks,
)
from .schedule import emptied_at_for, enabled_subtasks, is_done, is_full, item_active, item_done, level, todays_subtasks


# --- commands ---------------------------------------------------------------

@dataclass(frozen=True)
class AddBay:
    bay_id: str
    name: str


@dataclass(frozen=True)
class RenameBay:
    bay_id: str
    name: str


@dataclass(frozen=True)
class RemoveBay:
    bay_id: str


@dataclass(frozen=True)
class MoveBay:
    bay_id: str
    index: int


@dataclass(frozen=True)
class AddTask:
    task_id: str
    bay_id: str
    title: str
    recurrence: Recurrence = Recurrence()
    categories: tuple[str, ...] = ()
    parent_id: str | None = None
    target: int = 0


@dataclass(frozen=True)
class EditTask:
    task_id: str
    title: str | None = None
    recurrence: Recurrence | None = None
    categories: tuple[str, ...] | None = None
    target: int | None = None


@dataclass(frozen=True)
class SetProgress:
    """Set a counter task's count (clamped to 0..target); reaching the target completes it."""
    task_id: str
    value: int


@dataclass(frozen=True)
class RemoveTask:
    task_id: str


@dataclass(frozen=True)
class DuplicateTask:
    """Copy a task right below itself, among the same siblings (a subtask stays under its parent).
    A group is copied with all its steps. The copy starts fresh: unticked, no progress, timer or
    reminder. `new_id` names the copy; its steps get `new_id` plus a suffix."""
    task_id: str
    new_id: str


@dataclass(frozen=True)
class Complete:
    task_id: str


@dataclass(frozen=True)
class Uncomplete:
    task_id: str


@dataclass(frozen=True)
class MoveTask:
    task_id: str
    bay_id: str
    index: int


@dataclass(frozen=True)
class MoveToTop:
    task_id: str


@dataclass(frozen=True)
class MoveToBottom:
    task_id: str


@dataclass(frozen=True)
class SetEnabled:
    task_id: str
    enabled: bool


@dataclass(frozen=True)
class StartTimer:
    task_id: str
    seconds: int


@dataclass(frozen=True)
class StopTimer:
    task_id: str


@dataclass(frozen=True)
class SetReminder:
    task_id: str
    at: datetime


@dataclass(frozen=True)
class ClearReminder:
    task_id: str


@dataclass(frozen=True)
class RefillProgress:
    """How far the next refill has got, as the user sees it in the game: the time already passed,
    or the time still left. Either must be less than the refill time."""
    minutes: int
    remaining: bool = False  # True: `minutes` is the time left; False: the time passed


@dataclass(frozen=True)
class AddCooldown:
    cooldown_id: str
    title: str
    minutes: int
    capacity: int = 1
    value: int = 0  # how many it holds right now: 0..capacity
    progress: RefillProgress | None = None  # None: the next one takes a full refill from now


@dataclass(frozen=True)
class EditCooldown:
    cooldown_id: str
    title: str | None = None
    minutes: int | None = None
    capacity: int | None = None
    value: int | None = None  # set how many it holds right now (0..capacity)
    progress: RefillProgress | None = None  # set how far the next refill has got


@dataclass(frozen=True)
class RemoveCooldown:
    cooldown_id: str


@dataclass(frozen=True)
class MoveCooldown:
    cooldown_id: str
    index: int


@dataclass(frozen=True)
class Claim:
    """Claim whatever has refilled (allowed any time, even early): it empties to 0 and starts
    refilling from now."""
    cooldown_id: str


@dataclass(frozen=True)
class Unclaim:
    """Take back a claim (e.g. a mis-tap): the cooldown is full again."""
    cooldown_id: str


@dataclass(frozen=True)
class AddCountdown:
    """A standalone timer that ends `seconds` from now."""
    countdown_id: str
    title: str
    seconds: int


@dataclass(frozen=True)
class EditCountdown:
    countdown_id: str
    title: str | None = None
    seconds: int | None = None  # the new time left, from now


@dataclass(frozen=True)
class RestartCountdown:
    """Run it again for the same length, from now."""
    countdown_id: str


@dataclass(frozen=True)
class RemoveCountdown:
    """Delete it, or dismiss it once it's over."""
    countdown_id: str


Command = (
    AddBay | RenameBay | RemoveBay | MoveBay
    | AddTask | EditTask | RemoveTask | DuplicateTask | Complete | Uncomplete | SetProgress
    | MoveTask | MoveToTop | MoveToBottom | SetEnabled
    | StartTimer | StopTimer | SetReminder | ClearReminder
    | AddCooldown | EditCooldown | RemoveCooldown | MoveCooldown | Claim | Unclaim
    | AddCountdown | EditCountdown | RestartCountdown | RemoveCountdown
)


# --- events (for a future notifier / audit log) ------------------------------

@dataclass(frozen=True)
class Completed:
    task_ids: tuple[str, ...]
    at: datetime


@dataclass(frozen=True)
class Uncompleted:
    task_ids: tuple[str, ...]


@dataclass(frozen=True)
class Claimed:
    cooldown_id: str
    at: datetime
    amount: int  # how many had refilled


Event = Completed | Uncompleted | Claimed


# --- apply --------------------------------------------------------------------

def _clean_name(name: str) -> str:
    name = name.strip()
    if not name:
        raise CommandError("name must not be empty")
    return name


def _set(state: State, task: Task, **changes) -> State:
    return update_tasks(state, {task.id: replace(task, **changes)})


def _check_target(target: int) -> int:
    if not 0 <= target <= MAX_TARGET:
        raise CommandError(f"target must be between 0 and {MAX_TARGET}")
    return target


def _set_completed(state: State, task_ids: list[str], at: datetime | None) -> State:
    return update_tasks(state, {
        t.id: replace(t, completed_at=at) for t in state.tasks if t.id in task_ids
    })


def _reset_counters(state: State, task_ids: list[str]) -> State:
    return update_tasks(state, {
        t.id: replace(t, progress=0, progress_at=None)
        for t in state.tasks
        if t.id in task_ids and is_counter(t)
    })


def _require_available(state: State, task: Task, now: datetime) -> None:
    """'Not today' tasks can't be checked off, claimed, or counted (unchecking stays allowed)."""
    if not item_active(state, task, now):
        when = "right now (it's between windows)" if task.recurrence.kind == "windows" else "today"
        raise CommandError(f"“{task.title}” isn't available {when}")


def _fresh_copy(task: Task, new_id: str, **changes) -> Task:
    """Same settings (title, repeat, count target, tags, opt-out), none of the progress."""
    return replace(
        task, id=new_id, completed_at=None, timer=None, reminder_at=None, progress=0, progress_at=None,
        origin_id=None, **changes,
    )


def _require_new_id(state: State, new_id: str) -> None:
    if any(x.id == new_id for x in (*state.bays, *state.tasks, *state.cooldowns, *state.countdowns)):
        raise CommandError(f"id {new_id!r} already exists")


def _check_minutes(minutes: int) -> int:
    if not 1 <= minutes <= MAX_COOLDOWN_MINUTES:
        raise CommandError(f"cooldown must be between 1 and {MAX_COOLDOWN_MINUTES} minutes")
    return minutes


def _check_capacity(capacity: int) -> int:
    if not 1 <= capacity <= MAX_CAPACITY:
        raise CommandError(f"it must hold between 1 and {MAX_CAPACITY}")
    return capacity


def _elapsed(progress: RefillProgress, value: int, cooldown: Cooldown) -> int:
    """Minutes already passed on the next refill, from what the user entered."""
    if value >= cooldown.capacity:
        raise CommandError("it's full, so there's no refill in progress to set")
    m, refill = progress.minutes, cooldown.minutes
    if progress.remaining:
        if not 0 < m < refill:
            raise CommandError(f"the time left must be more than 0 and less than the refill time ({refill} min)")
        return refill - m
    if not 0 <= m < refill:
        raise CommandError(f"the time passed must be less than the refill time ({refill} min)")
    return m


def _check_value(value: int, capacity: int) -> int:
    if not 0 <= value <= capacity:
        raise CommandError(f"the current value must be between 0 and {capacity} (what it holds up to)")
    return value


def _check_seconds(seconds: int) -> int:
    if not 1 <= seconds <= MAX_TIMER_SECONDS:
        raise CommandError(f"a timer must be between 1 second and {MAX_TIMER_SECONDS // 86400} days")
    return seconds


def _countdown_for(countdown: Countdown, seconds: int, now: datetime) -> Countdown:
    """Set to end `seconds` from now."""
    return replace(countdown, ends_at=now + timedelta(seconds=seconds), seconds=seconds)


def _put_countdown(state: State, countdown: Countdown) -> State:
    return replace(state, countdowns=tuple(countdown if c.id == countdown.id else c for c in state.countdowns))


def _put_cooldown(state: State, cooldown: Cooldown) -> State:
    """Replace the cooldown with the same id."""
    return replace(state, cooldowns=tuple(cooldown if c.id == cooldown.id else c for c in state.cooldowns))


def _set_cooldown(state: State, cooldown: Cooldown, **changes) -> State:
    return _put_cooldown(state, replace(cooldown, **changes))


def apply(state: State, cmd: Command, now: datetime) -> tuple[State, list[Event]]:
    match cmd:
        case AddBay(bay_id, name):
            _require_new_id(state, bay_id)
            position = max((b.position for b in state.bays), default=-1) + 1
            return replace(state, bays=state.bays + (Bay(bay_id, _clean_name(name), position),)), []

        case RenameBay(bay_id, name):
            bay = find_bay(state, bay_id)
            bays = tuple(replace(b, name=_clean_name(name)) if b.id == bay.id else b for b in state.bays)
            return replace(state, bays=bays), []

        case RemoveBay(bay_id):
            find_bay(state, bay_id)
            state = replace(
                state,
                bays=tuple(b for b in state.bays if b.id != bay_id),
                tasks=tuple(t for t in state.tasks if t.bay_id != bay_id),
            )
            return ordering.compact_bays(state), []

        case MoveBay(bay_id, index):
            return ordering.move_bay(state, bay_id, index), []

        case AddTask(task_id, bay_id, title, recurrence, categories, parent_id, target):
            _require_new_id(state, task_id)
            find_bay(state, bay_id)
            if parent_id is not None:
                parent = find_task(state, parent_id)
                if parent.bay_id != bay_id or parent.parent_id is not None:
                    raise CommandError("subtasks must be one level deep, in the parent's bay")
            task = Task(
                id=task_id,
                bay_id=bay_id,
                title=_clean_name(title),
                position=ordering.next_position(tasks_in_bay(state, bay_id, parent_id)),
                recurrence=recurrence,
                categories=categories,
                parent_id=parent_id,
                target=_check_target(target),
            )
            return replace(state, tasks=state.tasks + (task,)), []

        case EditTask(task_id, title, recurrence, categories, target):
            task = find_task(state, task_id)
            return _set(
                state,
                task,
                title=task.title if title is None else _clean_name(title),
                recurrence=task.recurrence if recurrence is None else recurrence,
                categories=task.categories if categories is None else categories,
                target=task.target if target is None else _check_target(target),
            ), []

        case SetProgress(task_id, value):
            task = find_task(state, task_id)
            if not is_counter(task):
                raise CommandError("this task has no counter")
            _require_available(state, task, now)
            value = max(0, min(value, task.target))
            was_done = is_done(task, now)
            state = _set(state, task, progress=value, progress_at=now)
            if value >= task.target and not was_done:
                return _set_completed(state, [task_id], now), [Completed((task_id,), now)]
            if value < task.target and was_done:
                return _set_completed(state, [task_id], None), [Uncompleted((task_id,))]
            return state, []

        case DuplicateTask(task_id, new_id):
            task = find_task(state, task_id)
            copies = [_fresh_copy(task, new_id, title=f"{task.title} (copy)")] + [
                _fresh_copy(c, f"{new_id}-{i}", parent_id=new_id) for i, c in enumerate(subtasks(state, task))
            ]
            for copy in copies:
                _require_new_id(state, copy.id)
            state = replace(state, tasks=state.tasks + tuple(copies))
            return ordering.move_task(state, new_id, task.bay_id, task.position + 1), []

        case RemoveTask(task_id):
            task = find_task(state, task_id)
            gone = {task_id} | {c.id for c in subtasks(state, task)}
            state = replace(state, tasks=tuple(t for t in state.tasks if t.id not in gone))
            return ordering.renumber_siblings(state, task.bay_id, task.parent_id), []

        case Complete(task_id):
            task = find_task(state, task_id)
            if item_done(state, task, now):
                return state, []
            _require_available(state, task, now)
            ids = [c.id for c in todays_subtasks(state, task, now) if not is_done(c, now)] or [task.id]
            return _set_completed(state, ids, now), [Completed(tuple(ids), now)]

        case Uncomplete(task_id):
            task = find_task(state, task_id)
            if not item_done(state, task, now):
                return state, []
            ids = [c.id for c in enabled_subtasks(state, task)] or [task.id]
            state = _reset_counters(_set_completed(state, ids, None), ids)
            return state, [Uncompleted(tuple(ids))]

        case MoveTask(task_id, bay_id, index):
            return ordering.move_task(state, task_id, bay_id, index), []

        case MoveToTop(task_id):
            return ordering.move_to_top(state, task_id), []

        case MoveToBottom(task_id):
            return ordering.move_to_bottom(state, task_id), []

        case SetEnabled(task_id, enabled):
            return _set(state, find_task(state, task_id), enabled=enabled), []

        case StartTimer(task_id, seconds):
            if seconds <= 0:
                raise CommandError("timer must be positive")
            return _set(state, find_task(state, task_id), timer=Timer(now, seconds)), []

        case StopTimer(task_id):
            return _set(state, find_task(state, task_id), timer=None), []

        case SetReminder(task_id, at):
            return _set(state, find_task(state, task_id), reminder_at=at), []

        case ClearReminder(task_id):
            return _set(state, find_task(state, task_id), reminder_at=None), []

        case AddCooldown(cooldown_id, title, minutes, capacity, value, progress):
            _require_new_id(state, cooldown_id)
            cooldown = Cooldown(
                id=cooldown_id,
                title=_clean_name(title),
                position=ordering.next_position(list(state.cooldowns)),
                minutes=_check_minutes(minutes),
                capacity=_check_capacity(capacity),
            )
            value = _check_value(value, capacity)
            elapsed = 0 if progress is None else _elapsed(progress, value, cooldown)
            cooldown = replace(cooldown, emptied_at=emptied_at_for(value, cooldown, now, elapsed))
            return replace(state, cooldowns=state.cooldowns + (cooldown,)), []

        case EditCooldown(cooldown_id, title, minutes, capacity, value, progress):
            old = find_cooldown(state, cooldown_id)
            cooldown = replace(
                old,
                title=old.title if title is None else _clean_name(title),
                minutes=old.minutes if minutes is None else _check_minutes(minutes),
                capacity=old.capacity if capacity is None else _check_capacity(capacity),
            )
            if progress is not None:
                value = level(cooldown, now) if value is None else _check_value(value, cooldown.capacity)
                elapsed = _elapsed(progress, value, cooldown)
                cooldown = replace(cooldown, emptied_at=emptied_at_for(value, cooldown, now, elapsed))
            # Otherwise only an actual change of value restarts the refill; re-saving the value it
            # already has keeps the progress towards the next one.
            elif value is not None and _check_value(value, cooldown.capacity) != level(cooldown, now):
                cooldown = replace(cooldown, emptied_at=emptied_at_for(value, cooldown, now))
            return _put_cooldown(state, cooldown), []

        case RemoveCooldown(cooldown_id):
            find_cooldown(state, cooldown_id)
            state = replace(state, cooldowns=tuple(c for c in state.cooldowns if c.id != cooldown_id))
            return ordering.compact_cooldowns(state), []

        case MoveCooldown(cooldown_id, index):
            return ordering.move_cooldown(state, cooldown_id, index), []

        case Claim(cooldown_id):
            cooldown = find_cooldown(state, cooldown_id)
            amount = level(cooldown, now)
            return _set_cooldown(state, cooldown, emptied_at=now), [Claimed(cooldown_id, now, amount)]

        case Unclaim(cooldown_id):
            cooldown = find_cooldown(state, cooldown_id)
            if is_full(cooldown, now):
                return state, []
            return _set_cooldown(state, cooldown, emptied_at=None), []

        case AddCountdown(countdown_id, title, seconds):
            _require_new_id(state, countdown_id)
            blank = Countdown(id=countdown_id, title=_clean_name(title), ends_at=now, seconds=0)
            return replace(state, countdowns=state.countdowns + (_countdown_for(blank, _check_seconds(seconds), now),)), []

        case EditCountdown(countdown_id, title, seconds):
            countdown = find_countdown(state, countdown_id)
            if title is not None:
                countdown = replace(countdown, title=_clean_name(title))
            if seconds is not None:
                countdown = _countdown_for(countdown, _check_seconds(seconds), now)
            return _put_countdown(state, countdown), []

        case RestartCountdown(countdown_id):
            countdown = find_countdown(state, countdown_id)
            return _put_countdown(state, _countdown_for(countdown, countdown.seconds, now)), []

        case RemoveCountdown(countdown_id):
            find_countdown(state, countdown_id)
            return replace(state, countdowns=tuple(c for c in state.countdowns if c.id != countdown_id)), []

    raise CommandError(f"unknown command {cmd!r}")
