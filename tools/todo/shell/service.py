"""Shell operations shared by the web routes and the debug CLI."""

import secrets
import sqlite3
from collections.abc import Callable, Iterator
from datetime import datetime

from ..core import history, preset
from ..core.auth_rules import PRESET_USERNAME
from ..core.commands import Command, apply
from ..core.defaults import starter_state
from ..core.model import State
from ..core.view import missing_view
from . import auth, db

Step = Callable[[history.History[State]], history.History[State]]


def command_step(cmd: Command, now: datetime) -> Step:
    # Events are ignored for now; a notifier would consume them here.
    return lambda h: history.push(h, apply(h.present, cmd, now)[0])


def _insert_user(conn: sqlite3.Connection, username: str, password: str, now: datetime, state: State) -> int | None:
    user_id = db.create_user(conn, username, auth.hash_password(password), now)
    if user_id is not None:
        db.save_history(conn, user_id, history.History(state))
    return user_id


def preset_state(conn: sqlite3.Connection, now: datetime, ids: Iterator[str]) -> State:
    """The admin-managed preset. Seeded from the built-in starter template the first time."""
    user = db.get_user_by_username(conn, PRESET_USERNAME)
    if user is None:
        with db.write_transaction(conn):
            _insert_user(conn, PRESET_USERNAME, secrets.token_urlsafe(32), now, starter_state(ids))
        user = db.get_user_by_username(conn, PRESET_USERNAME)
    return db.load_state(conn, user["id"])


def preset_user_id(conn: sqlite3.Connection, now: datetime, ids: Iterator[str]) -> int:
    preset_state(conn, now, ids)
    return db.get_user_by_username(conn, PRESET_USERNAME)["id"]


def create_user(conn: sqlite3.Connection, username: str, password: str, now: datetime, ids: Iterator[str]) -> int | None:
    """Create a user starting from a copy of the current preset. None if the username is taken."""
    start = preset.instantiate(preset_state(conn, now, ids), ids)
    with db.write_transaction(conn):
        return _insert_user(conn, username, password, now, start)


def ensure_user(conn: sqlite3.Connection, username: str, now: datetime, ids: Iterator[str]) -> int:
    """Get or create a no-login account (random password nobody knows)."""
    user = db.get_user_by_username(conn, username)
    if user is not None:
        return user["id"]
    user_id = create_user(conn, username, secrets.token_urlsafe(32), now, ids)
    return user_id if user_id is not None else db.get_user_by_username(conn, username)["id"]


def suggested_step(conn: sqlite3.Connection, preset_task_id: str, do_import: bool, now: datetime,
                   ids: Iterator[str]) -> Step:
    """Import (or hide) one Suggested preset item."""
    current = preset_state(conn, now, ids)
    if do_import:
        return lambda h: history.push(h, preset.import_item(h.present, current, preset_task_id, ids))
    return lambda h: history.push(h, preset.hide(h.present, preset_task_id))


def import_step(conn: sqlite3.Connection, preset_ids: list[str], now: datetime, ids: Iterator[str]) -> Step:
    """Import the chosen preset items (from Browse) as one undoable step."""
    current = preset_state(conn, now, ids)
    return lambda h: history.push(h, preset.import_items(h.present, current, preset_ids, ids))


def missing_dicts(conn: sqlite3.Connection, state: State, now: datetime, ids: Iterator[str]) -> list[dict]:
    return missing_view(state, preset_state(conn, now, ids))


def suggested_dicts(conn: sqlite3.Connection, state: State, now: datetime, ids: Iterator[str]) -> list[dict]:
    return [s.__dict__ for s in preset.suggested(state, preset_state(conn, now, ids))]


def mutate(conn: sqlite3.Connection, user_id: int, step: Step) -> history.History[State]:
    """Load history, apply a pure step, persist if it changed. One write transaction."""
    with db.write_transaction(conn):
        h = db.load_history(conn, user_id)
        new_h = step(h)
        if new_h is not h:
            db.save_history(conn, user_id, new_h)
    return new_h
