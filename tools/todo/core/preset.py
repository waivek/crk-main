"""The admin-managed preset: what new users start with, plus ways for everyone else to get what
was added later.

Every bay/task/cooldown copied from the preset remembers its `origin_id`. What a user doesn't have
yet is `missing()`: top-level tasks and cooldowns, plus steps added to groups the user already has.
- Suggested nags about the top-level ones the user hasn't hidden (Import or Hide).
- Browse lists all of it, hidden ones included; the user ticks what they want and imports those.
Users own their copies; the preset never edits them. Pure: no I/O, no clock.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, replace
from typing import Literal

from .model import (
    Bay, CommandError, Cooldown, State, Task, bays_in_order, cooldowns_in_order, subtasks, tasks_in_bay, update_tasks,
)
from .ordering import next_position


@dataclass(frozen=True)
class Suggested:
    id: str          # the preset task's / cooldown's id
    title: str
    bay_name: str    # where it will go: a bay name, or COOLDOWNS
    steps: int       # number of subtasks


@dataclass(frozen=True)
class Missing:
    """A preset item the user doesn't have."""
    id: str                     # the preset task's / step's / cooldown's id
    kind: Literal["task", "step", "cooldown"]
    title: str
    bay_name: str               # where it goes: a bay name, or COOLDOWNS
    group: str | None           # a step: the title of the user's group it joins
    steps: int                  # a task: how many steps come with it
    hidden: bool                # the user hid it from Suggested
    parent_id: str | None = None  # a step: the preset group's id


COOLDOWNS = "Cooldowns"


# --- copying ------------------------------------------------------------------------

def _copy_task(src: Task, bay_id: str, parent_id: str | None, position: int, new_id: str) -> Task:
    return Task(
        id=new_id,
        bay_id=bay_id,
        title=src.title,
        position=position,
        recurrence=src.recurrence,
        categories=src.categories,
        parent_id=parent_id,
        target=src.target,
        origin_id=src.id,
    )


def _copy_with_subtasks(preset: State, src: Task, bay_id: str, position: int, ids: Iterator[str]) -> list[Task]:
    top = _copy_task(src, bay_id, None, position, next(ids))
    return [top] + [_copy_task(c, bay_id, top.id, i, next(ids)) for i, c in enumerate(subtasks(preset, src))]


def _copy_cooldown(src: Cooldown, position: int, new_id: str) -> Cooldown:
    """A copy holds what the preset's cooldown holds (and keeps refilling the same way), so the
    admin sets what new copies start with by setting the preset's current value."""
    return Cooldown(
        id=new_id,
        title=src.title,
        position=position,
        minutes=src.minutes,
        capacity=src.capacity,
        emptied_at=src.emptied_at,
        origin_id=src.id,
    )


def instantiate(preset: State, ids: Iterator[str]) -> State:
    """A new user's starting state: a copy of the whole preset."""
    bays: list[Bay] = []
    tasks: list[Task] = []
    for pos, pb in enumerate(bays_in_order(preset)):
        bay = Bay(next(ids), pb.name, pos, origin_id=pb.id)
        bays.append(bay)
        for i, t in enumerate(tasks_in_bay(preset, pb.id)):
            tasks += _copy_with_subtasks(preset, t, bay.id, i, ids)
    cooldowns = tuple(_copy_cooldown(c, i, next(ids)) for i, c in enumerate(cooldowns_in_order(preset)))
    return State(bays=tuple(bays), tasks=tuple(tasks), cooldowns=cooldowns)


# --- what the user doesn't have --------------------------------------------------------

def _copy_of(user: State, preset_task_id: str) -> Task | None:
    """The user's top-level copy of a preset task (a group), if they have one."""
    return next((t for t in user.tasks if t.origin_id == preset_task_id and t.parent_id is None), None)


def missing(user: State, preset: State) -> list[Missing]:
    """Preset items the user has no copy of, in preset order: top-level tasks (which bring their
    steps along), steps the admin added to groups the user already has, then cooldowns."""
    have = {x.origin_id for x in (*user.tasks, *user.cooldowns) if x.origin_id is not None}
    out = []
    for b in bays_in_order(preset):
        for t in tasks_in_bay(preset, b.id):
            steps = subtasks(preset, t)
            if t.id not in have:
                out.append(Missing(t.id, "task", t.title, b.name, None, len(steps), t.id in user.dismissed))
                continue
            group = _copy_of(user, t.id)
            if group is None:
                continue  # their copy became a step somewhere else; nothing sensible to add to
            out += [
                Missing(c.id, "step", c.title, b.name, group.title, 0, c.id in user.dismissed, parent_id=t.id)
                for c in steps if c.id not in have
            ]
    out += [
        Missing(c.id, "cooldown", c.title, COOLDOWNS, None, 0, c.id in user.dismissed)
        for c in cooldowns_in_order(preset) if c.id not in have
    ]
    return out


def suggested(user: State, preset: State) -> list[Suggested]:
    """Top-level preset tasks, then cooldowns, that the user has neither imported nor hidden,
    in preset order."""
    return [
        Suggested(m.id, m.title, m.bay_name, m.steps)
        for m in missing(user, preset)
        if m.kind != "step" and not m.hidden
    ]


def hide(user: State, preset_task_id: str) -> State:
    return replace(user, dismissed=user.dismissed | {preset_task_id})


def import_item(user: State, preset: State, preset_task_id: str, ids: Iterator[str]) -> State:
    return import_items(user, preset, [preset_task_id], ids)


def import_items(user: State, preset: State, preset_ids: Iterable[str], ids: Iterator[str]) -> State:
    """Copy the chosen missing preset items into the user's state, in the order given:
    - a task (with its steps) goes to the end of the matching bay, which is created if needed;
    - a step goes to the end of the user's copy of its group;
    - a cooldown goes to the end of their cooldowns."""
    available = {m.id: m for m in missing(user, preset)}
    wanted = list(dict.fromkeys(preset_ids))
    if not wanted:
        raise CommandError("pick something to import")
    if any(i not in available for i in wanted):
        raise CommandError("some of those are no longer missing from your list")
    for item in (available[i] for i in wanted):
        match item.kind:
            case "cooldown":
                src_cd = next(c for c in preset.cooldowns if c.id == item.id)
                copy = _copy_cooldown(src_cd, next_position(list(user.cooldowns)), next(ids))
                user = replace(user, cooldowns=user.cooldowns + (copy,))
            case "step":
                assert item.parent_id is not None
                group = _copy_of(user, item.parent_id)
                assert group is not None
                src = next(t for t in preset.tasks if t.id == item.id)
                position = next_position(tasks_in_bay(user, group.bay_id, group.id))
                user = replace(user, tasks=user.tasks + (_copy_task(src, group.bay_id, group.id, position, next(ids)),))
            case "task":
                src = next(t for t in preset.tasks if t.id == item.id)
                user, bay_id = _ensure_bay(user, preset, src.bay_id, ids)
                added = _copy_with_subtasks(preset, src, bay_id, next_position(tasks_in_bay(user, bay_id)), ids)
                user = replace(user, tasks=user.tasks + tuple(added))
    return user


def _ensure_bay(user: State, preset: State, preset_bay_id: str, ids: Iterator[str]) -> tuple[State, str]:
    for b in user.bays:
        if b.origin_id == preset_bay_id:
            return user, b.id
    name = next(b.name for b in preset.bays if b.id == preset_bay_id)
    bay = Bay(next(ids), name, max((b.position for b in user.bays), default=-1) + 1, origin_id=preset_bay_id)
    return replace(user, bays=user.bays + (bay,)), bay.id


# --- one-off: link existing copies to the preset ------------------------------------

def adopt_origins(user: State, preset: State) -> State:
    """Give origin ids to tasks/bays created before origins existed, matching by bay name and
    title path. Used once when turning an existing account into a preset-aware one."""
    bay_origin = {b.name: b.id for b in preset.bays}
    user = replace(user, bays=tuple(
        replace(b, origin_id=bay_origin.get(b.name)) if b.origin_id is None else b for b in user.bays))

    def path(state: State, t: Task) -> tuple[str, str | None, str]:
        bay = next((b.name for b in state.bays if b.id == t.bay_id), "")
        parent = next((p.title for p in state.tasks if p.id == t.parent_id), None)
        return (bay, parent, t.title)

    preset_by_path = {path(preset, t): t.id for t in preset.tasks}
    taken = {t.origin_id for t in user.tasks if t.origin_id}
    updated = {}
    for t in user.tasks:
        origin = preset_by_path.get(path(user, t))
        if t.origin_id is None and origin is not None and origin not in taken:
            updated[t.id] = replace(t, origin_id=origin)
            taken.add(origin)
    return update_tasks(user, updated)
