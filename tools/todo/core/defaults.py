"""The starter template every new user begins with."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from .commands import AddBay, AddCooldown, AddTask, apply
from .model import Recurrence, State

DAILY = Recurrence("daily")  # resets at the global reset (midnight KST)
EIGHT_HOURS = 8 * 60


@dataclass(frozen=True)
class Item:
    title: str
    subtasks: tuple["Item", ...] = ()  # ticking the parent ticks all of these
    target: int = 0                    # >= 2 makes it a counter
    recurrence: Recurrence = DAILY


STARTER_TEMPLATE: tuple[tuple[str, tuple[Item, ...]], ...] = (
    ("Main", (
        Item("Bounties"),
        Item("Starlight Island", (Item("Starlight Island 1"), Item("Starlight Island 2"), Item("Starlight Island 3"))),
        Item("Monster Menace", (Item("Red Chest"), Item("Blue Chest"), Item("Green Chest"))),
        Item("Ads", (Item("Gacha Ad"), Item("Normal Ads", target=5))),
        Item("Town Square", (Item("Mail Box"), Item("Cake Hound"), Item("Town Square Event"))),
        Item("Tree of Wishes", target=45),
        Item("Shop"),
    )),
)

# Claim, then wait 8 hours: (title, minutes).
STARTER_COOLDOWNS: tuple[tuple[str, int], ...] = (
    ("Fountain", EIGHT_HOURS),
    ("Harbour Ship", EIGHT_HOURS),
)

# AddBay / AddTask / AddCooldown do not depend on time; any fixed value keeps this pure.
_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)


def starter_state(ids: Iterator[str]) -> State:
    """Build the starter state. `ids` supplies fresh ids (the shell passes uuids)."""
    state = State()
    for bay_name, items in STARTER_TEMPLATE:
        bay_id = next(ids)
        state, _ = apply(state, AddBay(bay_id, bay_name), _EPOCH)
        for item in items:
            state = _add(state, item, bay_id, None, ids)
    for title, minutes in STARTER_COOLDOWNS:
        state, _ = apply(state, AddCooldown(next(ids), title, minutes), _EPOCH)
    return state


def _add(state: State, item: Item, bay_id: str, parent_id: str | None, ids: Iterator[str]) -> State:
    task_id = next(ids)
    cmd = AddTask(task_id, bay_id, item.title, item.recurrence, parent_id=parent_id, target=item.target)
    state, _ = apply(state, cmd, _EPOCH)
    for sub in item.subtasks:
        state = _add(state, sub, bay_id, task_id, ids)
    return state
