from datetime import UTC, datetime

from tools.todo.core.commands import AddBay, AddTask, Command, apply
from tools.todo.core.model import Recurrence, State

# 2026-09-25 is a Friday.
FRI = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def at(day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def run(state: State, *cmds: Command, now: datetime = FRI) -> State:
    for cmd in cmds:
        state, _ = apply(state, cmd, now)
    return state


def seeded(*titles: str, recurrence: Recurrence = Recurrence("daily")) -> State:
    """One bay 'main' with tasks whose ids equal their titles."""
    return run(State(), AddBay("main", "Daily"), *(AddTask(t, "main", t, recurrence) for t in titles))
