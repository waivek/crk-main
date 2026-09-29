"""Debug CLI: inspect and drive any user's todo state straight from SQLite (no login needed).

    uv run python -m tools.todo.debug users
    uv run python -m tools.todo.debug show vivek [--json] [--now 2026-09-25T15:00:00Z]
    uv run python -m tools.todo.debug cmd vivek '{"type": "complete", "task_id": "..."}' [--now ...]
    uv run python -m tools.todo.debug undo vivek
    uv run python -m tools.todo.debug redo vivek
    uv run python -m tools.todo.debug make-admin vivek     (or revoke-admin)
    uv run python -m tools.todo.debug show preset          (the admin-managed preset)

--now pretends the clock is at that instant (handy for checking resets and cooldowns).
The database is TODO_DB, or tools/todo/data/todo.sqlite3 by default.
"""

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime

from .core import history
from .core.auth_rules import PRESET_USERNAME
from .core.codec import CodecError, dt_from_str, parse_command
from .core.model import CommandError
from .core.text_view import render_text
from .core.view import build_view
from .shell import db, service
from .shell.web import DEFAULT_DB


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.todo.debug", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["users", "show", "cmd", "undo", "redo", "make-admin", "revoke-admin"])
    parser.add_argument("username", nargs="?")
    parser.add_argument("payload", nargs="?", help="command JSON (for cmd)")
    parser.add_argument("--json", action="store_true", help="print the raw view JSON")
    parser.add_argument("--now", help="ISO datetime with timezone to use as the current time")
    args = parser.parse_args(argv)

    now = dt_from_str(args.now) if args.now else datetime.now(UTC)
    conn = db.connect(os.environ.get("TODO_DB", str(DEFAULT_DB)))
    db.migrate(conn)

    if args.action == "users":
        for r in conn.execute("SELECT id, username, created_at, is_admin FROM users ORDER BY id"):
            print(f"{r['id']}\t{r['username']}\t{r['created_at']}{'\tadmin' if r['is_admin'] else ''}")
        return 0

    if not args.username:
        parser.error("username is required")
    user = db.get_user_by_username(conn, args.username)
    if user is None:
        print(f"no such user: {args.username}", file=sys.stderr)
        return 1

    if args.action in ("make-admin", "revoke-admin"):
        with db.write_transaction(conn):
            db.set_admin(conn, user["id"], args.action == "make-admin")
        print(f"{args.username}: admin={'yes' if args.action == 'make-admin' else 'no'}")
        return 0

    try:
        match args.action:
            case "cmd":
                if not args.payload:
                    parser.error("cmd needs a JSON payload")
                step = service.command_step(parse_command(json.loads(args.payload), uuid.uuid4().hex), now)
                h = service.mutate(conn, user["id"], step)
            case "undo":
                h = service.mutate(conn, user["id"], history.undo)
            case "redo":
                h = service.mutate(conn, user["id"], history.redo)
            case _:
                h = db.load_history(conn, user["id"])
    except (CodecError, CommandError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    view = build_view(h.present, now, can_undo=h.can_undo, can_redo=h.can_redo)
    if args.username != PRESET_USERNAME:
        view["suggested"] = service.suggested_dicts(conn, h.present, now, iter(lambda: uuid.uuid4().hex, None))
    print(json.dumps(view, indent=2) if args.json else render_text(view))
    return 0


if __name__ == "__main__":
    sys.exit(main())
