"""Time rules: when a task counts as done, when it resets, when it is active."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from . import config
from .model import Cooldown, Recurrence, State, Task, Timer, Window, is_counter, subtasks

DAY = timedelta(days=1)
WEEK = timedelta(days=7)


def daily_reset_at_or_before(now: datetime) -> datetime:
    """The most recent daily reset boundary (<= now), in UTC."""
    boundary = datetime.combine(now.astimezone(config.RESET_TZ).date(), config.DAILY_RESET, tzinfo=config.RESET_TZ)
    if boundary > now:
        boundary -= DAY
    return boundary.astimezone(UTC)


def game_weekday(now: datetime) -> int:
    """Weekday of the current game day, in the reset timezone."""
    return daily_reset_at_or_before(now).astimezone(config.RESET_TZ).weekday()


def weekly_reset_at_or_before(now: datetime, weekday: int) -> datetime:
    return daily_reset_at_or_before(now) - timedelta(days=(game_weekday(now) - weekday) % 7)


def interval_reset_at_or_before(now: datetime, every: int, anchor: date) -> datetime:
    """Most recent reset of an every-N-days task: a daily reset whose game day is a whole
    number of cycles away from `anchor`."""
    start = daily_reset_at_or_before(now)
    game_day = start.astimezone(config.RESET_TZ).date()
    return start - timedelta(days=(game_day - anchor).days % every)


# --- time windows: open at set times every day (e.g. 00:00–00:30, 02:00–02:30, …) -------------

def _windows_around(windows: tuple[Window, ...], now: datetime) -> list[tuple[datetime, datetime]]:
    """Yesterday's, today's and tomorrow's windows as (opens, closes) datetimes, in order."""
    today = daily_reset_at_or_before(now)
    return [
        (day + timedelta(minutes=start), day + timedelta(minutes=end))
        for day in (today - DAY, today, today + DAY)
        for start, end in windows
    ]


def current_window(windows: tuple[Window, ...], now: datetime) -> tuple[datetime, datetime] | None:
    """The window open right now, if any."""
    return next(((o, c) for o, c in _windows_around(windows, now) if o <= now < c), None)


def last_window_opened(windows: tuple[Window, ...], now: datetime) -> datetime:
    """When the most recent window opened (the current one, or the last one before now)."""
    return max(o for o, _ in _windows_around(windows, now) if o <= now)


def next_window_opens(windows: tuple[Window, ...], now: datetime) -> datetime:
    return min(o for o, _ in _windows_around(windows, now) if o > now)


def last_reset(task: Task, now: datetime) -> datetime | None:
    """The boundary after which a completion counts. None means completion never expires."""
    match task.recurrence.kind:
        case "daily" | "days_of_week":
            return daily_reset_at_or_before(now)
        case "weekly":
            return weekly_reset_at_or_before(now, task.recurrence.weekday)
        case "interval":
            r = task.recurrence
            assert r.anchor is not None
            return interval_reset_at_or_before(now, r.every, r.anchor)
        case "windows":
            return last_window_opened(task.recurrence.windows, now)
        case _:
            return None


def is_done(task: Task, now: datetime) -> bool:
    if task.completed_at is None:
        return False
    boundary = last_reset(task, now)
    return boundary is None or task.completed_at >= boundary


def resets_at(task: Task, now: datetime) -> datetime | None:
    """When a currently-done task becomes available again (None if not done or never)."""
    if not is_done(task, now):
        return None
    match task.recurrence.kind:
        case "daily" | "days_of_week":
            return daily_reset_at_or_before(now) + DAY
        case "weekly":
            return weekly_reset_at_or_before(now, task.recurrence.weekday) + WEEK
        case "interval":
            boundary = last_reset(task, now)
            assert boundary is not None
            return boundary + timedelta(days=task.recurrence.every)
        case "windows":
            return next_window_opens(task.recurrence.windows, now)
        case _:
            return None


def progress(task: Task, now: datetime) -> int:
    """Current count for a counter task. Resets along with the task's completion."""
    if not is_counter(task):
        return int(is_done(task, now))
    if is_done(task, now):
        return task.target
    if task.progress_at is None:
        return 0
    if task.completed_at is not None and task.progress_at <= task.completed_at:
        return 0  # progress that led to a completion which has since expired
    boundary = last_reset(task, now)
    return task.progress if boundary is None or task.progress_at >= boundary else 0


def next_reset(recurrence: Recurrence, now: datetime) -> datetime | None:
    """If a task with this repeat were done right now, when would it become available again?
    (None for one-off tasks.) Used to preview a repeat setting while editing it."""
    probe = Task(id="", bay_id="", title="", position=0, recurrence=recurrence, completed_at=now)
    return resets_at(probe, now)


def is_active(task: Task, now: datetime) -> bool:
    """Day-restricted tasks are only active on their game days. A weekly task may also be
    limited to some days (e.g. active Mon–Sat, with Sunday as a tally day). A time-window task is
    only active while one of its windows is open."""
    r = task.recurrence
    if r.kind == "days_of_week" or (r.kind == "weekly" and r.days):
        return game_weekday(now) in r.days
    if r.kind == "windows":
        return current_window(r.windows, now) is not None
    return True


def enabled_subtasks(state: State, task: Task) -> list[Task]:
    return [c for c in subtasks(state, task) if c.enabled]


def todays_subtasks(state: State, task: Task, now: datetime) -> list[Task]:
    """Opted-in subtasks that are available today."""
    return [c for c in enabled_subtasks(state, task) if is_active(c, now)]


def item_done(state: State, task: Task, now: datetime) -> bool:
    """A task with (opted-in) subtasks is done when all of today's are done. Steps that are
    'not today' don't hold it back (if none are available today, all of them count)."""
    children = todays_subtasks(state, task, now) or enabled_subtasks(state, task)
    return all(is_done(c, now) for c in children) if children else is_done(task, now)


def item_active(state: State, task: Task, now: datetime) -> bool:
    children = enabled_subtasks(state, task)
    return any(is_active(c, now) for c in children) if children else is_active(task, now)


def level(cooldown: Cooldown, now: datetime) -> int:
    """How many have refilled: one per `minutes` since it was emptied, up to capacity."""
    if cooldown.emptied_at is None:
        return cooldown.capacity
    refilled = (now - cooldown.emptied_at) // timedelta(minutes=cooldown.minutes)
    return max(0, min(cooldown.capacity, refilled))


def is_full(cooldown: Cooldown, now: datetime) -> bool:
    return level(cooldown, now) == cooldown.capacity


def emptied_at_for(value: int, cooldown: Cooldown, now: datetime, elapsed: int = 0) -> datetime | None:
    """The `emptied_at` that makes `cooldown` hold `value` right now (None when that's full),
    with the next one `elapsed` minutes into its refill (0: a full refill from now)."""
    if value >= cooldown.capacity:
        return None
    return now - value * timedelta(minutes=cooldown.minutes) - timedelta(minutes=elapsed)


def next_refill_at(cooldown: Cooldown, now: datetime) -> datetime | None:
    """When the next one refills (None if full)."""
    if is_full(cooldown, now):
        return None
    assert cooldown.emptied_at is not None
    return cooldown.emptied_at + (level(cooldown, now) + 1) * timedelta(minutes=cooldown.minutes)


def full_at(cooldown: Cooldown, now: datetime) -> datetime | None:
    """When it will be full again (None if it already is)."""
    if is_full(cooldown, now):
        return None
    assert cooldown.emptied_at is not None
    return cooldown.emptied_at + cooldown.capacity * timedelta(minutes=cooldown.minutes)


def timer_ends_at(timer: Timer) -> datetime:
    return timer.started_at + timedelta(seconds=timer.seconds)


def timer_remaining(timer: Timer, now: datetime) -> int:
    return max(0, int((timer_ends_at(timer) - now).total_seconds()))


def is_reminder_due(task: Task, now: datetime) -> bool:
    return (
        task.enabled
        and task.reminder_at is not None
        and task.reminder_at <= now
        and not is_done(task, now)
    )


@dataclass(frozen=True)
class ReminderDue:
    task_id: str
    title: str
    at: datetime


def due_reminders(state: State, now: datetime) -> list[ReminderDue]:
    """Notification events for a future notifier (email / Telegram) to deliver."""
    return [
        ReminderDue(t.id, t.title, t.reminder_at)
        for t in state.tasks
        if t.reminder_at is not None and is_reminder_due(t, now)
    ]
