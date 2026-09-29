"""SQLite persistence. Imperative shell: no business rules live here."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..core import codec
from ..core.history import History
from ..core.model import State

MIGRATIONS = [
    """
    CREATE TABLE users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        pw_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        history_cursor INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE bays (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL,
        position INTEGER NOT NULL
    );
    CREATE INDEX bays_user ON bays(user_id);
    CREATE TABLE tasks (
        id TEXT PRIMARY KEY,
        bay_id TEXT NOT NULL REFERENCES bays(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        position INTEGER NOT NULL,
        enabled INTEGER NOT NULL,
        recurrence_json TEXT NOT NULL,
        completed_at TEXT,
        link_group TEXT,
        timer_json TEXT,
        reminder_at TEXT,
        categories_json TEXT NOT NULL
    );
    CREATE INDEX tasks_bay ON tasks(bay_id);
    CREATE TABLE history (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL,
        state_json TEXT NOT NULL,
        PRIMARY KEY (user_id, seq)
    );
    """,
    "ALTER TABLE tasks ADD COLUMN parent_id TEXT",
    """
    ALTER TABLE tasks ADD COLUMN target INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE tasks ADD COLUMN progress INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE tasks ADD COLUMN progress_at TEXT
    """,
    """
    ALTER TABLE tasks ADD COLUMN ends_at TEXT;
    ALTER TABLE tasks ADD COLUMN origin_id TEXT;
    ALTER TABLE bays ADD COLUMN origin_id TEXT;
    ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE users ADD COLUMN dismissed_json TEXT NOT NULL DEFAULT '[]'
    """,
    "ALTER TABLE tasks DROP COLUMN ends_at",  # end dates were dropped as a feature
    "ALTER TABLE tasks DROP COLUMN link_group",  # linked tasks were dropped; subtasks cover it
    # Cooldowns got their own tab. Old "cooldown" task rows are moved over by the codec on load.
    """
    CREATE TABLE cooldowns (
        id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        title TEXT NOT NULL,
        position INTEGER NOT NULL,
        minutes INTEGER NOT NULL,
        early INTEGER NOT NULL,
        claimed_at TEXT,
        origin_id TEXT
    );
    CREATE INDEX cooldowns_user ON cooldowns(user_id)
    """,
    # Cooldowns refill up to a capacity (a plain one holds 1); everything may be claimed early.
    """
    ALTER TABLE cooldowns ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE cooldowns ADD COLUMN start_empty INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE cooldowns RENAME COLUMN claimed_at TO emptied_at;
    ALTER TABLE cooldowns DROP COLUMN early
    """,
    # "Start empty" became "starts at" a number (0 by default, less than the capacity).
    """
    ALTER TABLE cooldowns ADD COLUMN start INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE cooldowns DROP COLUMN start_empty
    """,
    # "Starts at" became "current value", which is just the level (derived from emptied_at).
    "ALTER TABLE cooldowns DROP COLUMN start",
]


def connect(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)  # explicit transactions below
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    with write_transaction(conn):
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            for statement in filter(str.strip, sql.split(";")):
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {i}")


@contextmanager
def write_transaction(conn: sqlite3.Connection):
    """BEGIN IMMEDIATE so concurrent gunicorn workers serialize read-modify-write."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


# --- users ----------------------------------------------------------------------

def create_user(conn: sqlite3.Connection, username: str, pw_hash: str, now: datetime) -> int | None:
    """Returns the new user id, or None if the username is taken. Call inside a transaction."""
    try:
        cur = conn.execute(
            "INSERT INTO users (username, pw_hash, created_at) VALUES (?, ?, ?)",
            (username, pw_hash, codec.dt_to_str(now)),
        )
    except sqlite3.IntegrityError:
        return None
    return cur.lastrowid


def get_user_by_username(conn: sqlite3.Connection, username: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()


def get_user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def set_admin(conn: sqlite3.Connection, user_id: int, is_admin: bool) -> None:
    conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))


# --- state ----------------------------------------------------------------------

def load_state(conn: sqlite3.Connection, user_id: int) -> State:
    bays = conn.execute("SELECT * FROM bays WHERE user_id = ?", (user_id,)).fetchall()
    tasks = conn.execute(
        "SELECT tasks.* FROM tasks JOIN bays ON bays.id = tasks.bay_id WHERE bays.user_id = ?",
        (user_id,),
    ).fetchall()
    cooldowns = conn.execute("SELECT * FROM cooldowns WHERE user_id = ?", (user_id,)).fetchall()
    # Through the codec's state dict, so rows saved before a model change are upgraded too.
    return codec.state_from_dict({
        "bays": [{"id": r["id"], "name": r["name"], "position": r["position"], "origin_id": r["origin_id"]} for r in bays],
        "tasks": [_task_dict(r) for r in tasks],
        "dismissed": json.loads(get_user(conn, user_id)["dismissed_json"]),
        "cooldowns": [dict(r) for r in cooldowns],
    })


def _task_dict(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "bay_id": r["bay_id"],
        "title": r["title"],
        "position": r["position"],
        "enabled": r["enabled"],
        "recurrence": json.loads(r["recurrence_json"]),
        "completed_at": r["completed_at"],
        "timer": json.loads(r["timer_json"]) if r["timer_json"] else None,
        "reminder_at": r["reminder_at"],
        "categories": json.loads(r["categories_json"]),
        "parent_id": r["parent_id"],
        "target": r["target"],
        "progress": r["progress"],
        "progress_at": r["progress_at"],
        "origin_id": r["origin_id"],
    }


def save_state(conn: sqlite3.Connection, user_id: int, state: State) -> None:
    """Replace all of a user's rows. Call inside a transaction."""
    conn.execute("DELETE FROM bays WHERE user_id = ?", (user_id,))  # cascades to tasks
    conn.execute("DELETE FROM cooldowns WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO cooldowns (id, user_id, title, position, minutes, capacity, emptied_at, origin_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [(c.id, user_id, c.title, c.position, c.minutes, c.capacity, codec.dt_to_str(c.emptied_at),
          c.origin_id)
         for c in state.cooldowns],
    )
    conn.executemany(
        "INSERT INTO bays (id, user_id, name, position, origin_id) VALUES (?, ?, ?, ?, ?)",
        [(b.id, user_id, b.name, b.position, b.origin_id) for b in state.bays],
    )
    conn.execute("UPDATE users SET dismissed_json = ? WHERE id = ?", (json.dumps(sorted(state.dismissed)), user_id))
    rows = []
    for t in state.tasks:
        d = codec.task_to_dict(t)
        rows.append((
            d["id"], d["bay_id"], d["title"], d["position"], int(d["enabled"]),
            json.dumps(d["recurrence"]), d["completed_at"],
            json.dumps(d["timer"]) if d["timer"] else None, d["reminder_at"],
            json.dumps(d["categories"]), d["parent_id"], d["target"], d["progress"], d["progress_at"],
            d["origin_id"],
        ))
    conn.executemany(
        "INSERT INTO tasks (id, bay_id, title, position, enabled, recurrence_json, completed_at,"
        " timer_json, reminder_at, categories_json, parent_id, target, progress, progress_at,"
        " origin_id)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )


# --- undo history -----------------------------------------------------------------

def load_history(conn: sqlite3.Connection, user_id: int) -> History[State]:
    rows = conn.execute("SELECT state_json FROM history WHERE user_id = ? ORDER BY seq", (user_id,)).fetchall()
    if not rows:
        return History(load_state(conn, user_id))
    states = [codec.state_from_dict(json.loads(r["state_json"])) for r in rows]
    cursor = get_user(conn, user_id)["history_cursor"]
    return History(present=states[cursor], past=tuple(states[:cursor]), future=tuple(states[cursor + 1:]))


def save_history(conn: sqlite3.Connection, user_id: int, h: History[State]) -> None:
    """Persist the history and make its present the user's current state. Call inside a transaction."""
    states = [*h.past, h.present, *h.future]
    conn.execute("DELETE FROM history WHERE user_id = ?", (user_id,))
    conn.executemany(
        "INSERT INTO history (user_id, seq, state_json) VALUES (?, ?, ?)",
        [(user_id, i, json.dumps(codec.state_to_dict(s))) for i, s in enumerate(states)],
    )
    conn.execute("UPDATE users SET history_cursor = ? WHERE id = ?", (len(h.past), user_id))
    save_state(conn, user_id, h.present)
