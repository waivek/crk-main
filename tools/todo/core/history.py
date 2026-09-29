from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from . import config

T = TypeVar("T")


@dataclass(frozen=True)
class History(Generic[T]):
    present: T
    past: tuple[T, ...] = ()    # oldest first
    future: tuple[T, ...] = ()  # next redo first

    @property
    def can_undo(self) -> bool:
        return bool(self.past)

    @property
    def can_redo(self) -> bool:
        return bool(self.future)


def push(h: History[T], new: T, limit: int = config.HISTORY_LIMIT) -> History[T]:
    """Record a new present. Clears redo. Keeps at most `limit` undo steps."""
    if new == h.present:
        return h
    past = (h.past + (h.present,))[-limit:] if limit > 0 else ()
    return History(present=new, past=past, future=())


def undo(h: History[T]) -> History[T]:
    if not h.past:
        return h
    return replace(h, present=h.past[-1], past=h.past[:-1], future=(h.present,) + h.future)


def redo(h: History[T]) -> History[T]:
    if not h.future:
        return h
    return replace(h, present=h.future[0], past=h.past + (h.present,), future=h.future[1:])
