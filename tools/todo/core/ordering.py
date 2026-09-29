from dataclasses import replace

from .model import (
    Bay, Cooldown, State, Task, bays_in_order, cooldowns_in_order, find_bay, find_cooldown, find_task, subtasks,
    tasks_in_bay, update_tasks,
)


def _renumbered(tasks: list[Task], bay_id: str, parent_id: str | None) -> dict[str, Task]:
    return {t.id: replace(t, bay_id=bay_id, parent_id=parent_id, position=i) for i, t in enumerate(tasks)}


def renumber_siblings(state: State, bay_id: str, parent_id: str | None) -> State:
    return update_tasks(state, _renumbered(tasks_in_bay(state, bay_id, parent_id), bay_id, parent_id))


def move_task(state: State, task_id: str, bay_id: str, index: int) -> State:
    """Move a task to `index` (clamped) among its siblings. Moving to another bay takes a
    parent's subtasks along, and turns a subtask into a top-level task."""
    task = find_task(state, task_id)
    find_bay(state, bay_id)
    parent_id = task.parent_id if bay_id == task.bay_id else None
    same_group = (bay_id, parent_id) == (task.bay_id, task.parent_id)
    source = [t for t in tasks_in_bay(state, task.bay_id, task.parent_id) if t.id != task_id]
    target = source if same_group else tasks_in_bay(state, bay_id, parent_id)
    target.insert(max(0, min(index, len(target))), task)
    updated = _renumbered(target, bay_id, parent_id)
    if not same_group:
        updated |= _renumbered(source, task.bay_id, task.parent_id)
        updated |= {c.id: replace(c, bay_id=bay_id) for c in subtasks(state, task)}
    return update_tasks(state, updated)


def move_to_top(state: State, task_id: str) -> State:
    return move_task(state, task_id, find_task(state, task_id).bay_id, 0)


def move_to_bottom(state: State, task_id: str) -> State:
    task = find_task(state, task_id)
    return move_task(state, task_id, task.bay_id, len(tasks_in_bay(state, task.bay_id, task.parent_id)))


def move_bay(state: State, bay_id: str, index: int) -> State:
    bay = find_bay(state, bay_id)
    others = [b for b in bays_in_order(state) if b.id != bay_id]
    others.insert(max(0, min(index, len(others))), bay)
    return _with_bay_order(state, others)


def compact_bays(state: State) -> State:
    return _with_bay_order(state, bays_in_order(state))


def _with_bay_order(state: State, ordered: list[Bay]) -> State:
    positions = {b.id: i for i, b in enumerate(ordered)}
    return replace(state, bays=tuple(replace(b, position=positions[b.id]) for b in state.bays))


def move_cooldown(state: State, cooldown_id: str, index: int) -> State:
    cooldown = find_cooldown(state, cooldown_id)
    others = [c for c in cooldowns_in_order(state) if c.id != cooldown_id]
    others.insert(max(0, min(index, len(others))), cooldown)
    return _with_cooldown_order(state, others)


def compact_cooldowns(state: State) -> State:
    return _with_cooldown_order(state, cooldowns_in_order(state))


def _with_cooldown_order(state: State, ordered: list[Cooldown]) -> State:
    positions = {c.id: i for i, c in enumerate(ordered)}
    return replace(state, cooldowns=tuple(replace(c, position=positions[c.id]) for c in state.cooldowns))


def next_position(items: list[Task] | list[Cooldown]) -> int:
    if not items:
        return 0
    return max(t.position for t in items) + 1
