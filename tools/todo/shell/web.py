"""Flask blueprint: the imperative shell. Reads the clock, makes ids, talks to SQLite,
and delegates every decision to the functional core."""

import os
import secrets
import uuid
from functools import wraps
from datetime import UTC, datetime, timedelta
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, redirect, render_template, request, url_for

from ..core import history
from ..core.auth_rules import PRESET_USERNAME, ROBOT_USERNAME, normalize_username, signup_errors
from ..core.codec import CodecError, dt_from_str, dt_to_str, parse_command, recurrence_from_dict
from ..core.model import CommandError
from ..core.schedule import next_reset
from ..core.text_view import render_text
from ..core.view import build_view, filter_view
from . import auth, db, service

TODO_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = TODO_DIR / "data" / "todo.sqlite3"

todo_bp = Blueprint(
    "todo",
    __name__,
    template_folder=str(TODO_DIR / "templates"),
    static_folder=str(TODO_DIR / "static"),
    static_url_path="/static",
)

_migrated: set[str] = set()


@todo_bp.context_processor
def _asset_version() -> dict:
    """Cache-busting token for static URLs: changes whenever app.css / app.js change on disk."""
    static = TODO_DIR / "static"
    return {"asset_v": int(max((static / f).stat().st_mtime for f in ("app.css", "app.js")))}


@todo_bp.record_once
def _configure(state) -> None:
    app = state.app
    app.config.setdefault("TODO_DB", os.environ.get("TODO_DB", str(DEFAULT_DB)))
    app.config.setdefault("SESSION_COOKIE_SAMESITE", "Lax")
    app.config.setdefault("PERMANENT_SESSION_LIFETIME", timedelta(days=30))
    if not app.secret_key:
        app.secret_key = os.environ.get("TODO_SECRET_KEY") or _load_or_create_secret(
            Path(app.config["TODO_DB"]).parent / "secret_key"
        )
    app.teardown_appcontext(_close_db)


def _load_or_create_secret(path: Path) -> str:
    """Persisted so sessions survive restarts and are shared by all gunicorn workers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text().strip()
    with os.fdopen(fd, "w") as f:
        key = secrets.token_hex(32)
        f.write(key)
    return key


def _conn():
    if "todo_db" not in g:
        path = current_app.config["TODO_DB"]
        g.todo_db = db.connect(path)
        if path not in _migrated:
            db.migrate(g.todo_db)
            _migrated.add(path)
    return g.todo_db


def _close_db(_exc) -> None:
    conn = g.pop("todo_db", None)
    if conn is not None:
        conn.close()


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


# --- workspaces ------------------------------------------------------------------------
# A page / API call works on one of three accounts' state:
#   "me"      the logged-in user                       /          /api/...
#   "robot"   a public, no-login debug account         /robot/    /robot/api/...
#   "preset"  the admin-managed preset (admins only)   /admin/    /admin/api/...
# The preset is what new users start with; items added to it later show up as "Suggested".

def _ids():
    return iter(_new_id, None)


def _is_admin() -> bool:
    user_id = auth.current_user_id()
    user = db.get_user(_conn(), user_id) if user_id is not None else None
    return bool(user and user["is_admin"])


def _workspace_user(ws: str) -> int:
    match ws:
        case "robot":
            return service.ensure_user(_conn(), ROBOT_USERNAME, _now(), _ids())
        case "preset":
            return service.preset_user_id(_conn(), _now(), _ids())
        case _:
            return auth.current_user_id()


def _view_for(ws: str, h: history.History | None = None, now: datetime | None = None) -> dict:
    now = now or _now()
    h = h or db.load_history(_conn(), _workspace_user(ws))
    view = build_view(h.present, now, can_undo=h.can_undo, can_redo=h.can_redo)
    view["suggested"] = [] if ws == "preset" else service.suggested_dicts(_conn(), h.present, now, _ids())
    return filter_view(view, request.args.get("category"), hide_done=request.args.get("hide_done") == "1")


def _render_app(ws: str, username: str):
    return render_template(
        "todo/app.html",
        username=username,
        csrf=auth.csrf_token() if ws != "robot" else "",
        mode=ws,
        is_admin=ws == "me" and _is_admin(),
        api_base=url_for({"me": "todo.index", "robot": "todo.robot_index", "preset": "todo.admin_index"}[ws]) + "api/",
        initial_view=_view_for(ws),
    )


# --- pages -------------------------------------------------------------------------

@todo_bp.get("/")
@auth.page_login_required
def index():
    user = db.get_user(_conn(), auth.current_user_id())
    if user is None:
        auth.logout()
        return redirect(url_for("todo.login_page"))
    return _render_app("me", user["username"])


@todo_bp.get("/robot/")
def robot_index():
    return _render_app("robot", ROBOT_USERNAME)


@todo_bp.get("/admin/")
@auth.page_login_required
def admin_index():
    if not _is_admin():
        return "Admins only.", 403
    return _render_app("preset", PRESET_USERNAME)


@todo_bp.get("/login")
def login_page():
    return render_template("todo/login.html", csrf=auth.csrf_token(), errors=[], username="")


@todo_bp.post("/login")
def login_submit():
    if not auth.csrf_ok(request.form.get("csrf")):
        return render_template("todo/login.html", csrf=auth.csrf_token(), errors=["Form expired, try again."], username=""), 400
    username = normalize_username(request.form.get("username", ""))
    user = db.get_user_by_username(_conn(), username)
    if not auth.verify_password(user["pw_hash"] if user else None, request.form.get("password", "")):
        return render_template("todo/login.html", csrf=auth.csrf_token(), errors=["Wrong username or password."], username=username), 401
    auth.login(user["id"])
    return redirect(url_for("todo.index"))


@todo_bp.get("/signup")
def signup_page():
    return render_template("todo/signup.html", csrf=auth.csrf_token(), errors=[], username="")


@todo_bp.post("/signup")
def signup_submit():
    form = request.form
    username = normalize_username(form.get("username", ""))
    errors = [] if auth.csrf_ok(form.get("csrf")) else ["Form expired, try again."]
    errors += signup_errors(username, form.get("password", ""), form.get("confirm", ""))
    if not errors:
        user_id = service.create_user(_conn(), username, form["password"], _now(), _ids())
        if user_id is None:
            errors.append("That username is taken.")
    if errors:
        return render_template("todo/signup.html", csrf=auth.csrf_token(), errors=errors, username=username), 400
    auth.login(user_id)
    return redirect(url_for("todo.index"))


@todo_bp.post("/logout")
def logout():
    if auth.csrf_ok(request.form.get("csrf")):
        auth.logout()
    return redirect(url_for("todo.login_page"))


# --- JSON API ----------------------------------------------------------------------

def _api_command(ws: str):
    try:
        cmd = parse_command(request.get_json(silent=True), _new_id())
        h = service.mutate(_conn(), _workspace_user(ws), service.command_step(cmd, _now()))
    except (CodecError, CommandError) as e:
        return jsonify(error=str(e)), 400
    return jsonify(_view_for(ws, h))


def _api_history(ws: str, which: str):
    h = service.mutate(_conn(), _workspace_user(ws), history.undo if which == "undo" else history.redo)
    return jsonify(_view_for(ws, h))


def _api_suggested(ws: str):
    """{"id": "<preset task id>", "action": "import" | "hide"}"""
    body = request.get_json(silent=True) or {}
    item_id, action = body.get("id"), body.get("action")
    if ws == "preset" or not isinstance(item_id, str) or action not in ("import", "hide"):
        return jsonify(error="bad suggested-item request"), 400
    try:
        step = service.suggested_step(_conn(), item_id, action == "import", _now(), _ids())
        h = service.mutate(_conn(), _workspace_user(ws), step)
    except CommandError as e:
        return jsonify(error=str(e)), 400
    return jsonify(_view_for(ws, h))


def _api_missing(ws: str):
    """Browse: preset items this account doesn't have. (The preset itself has everything.)"""
    if ws == "preset":
        return jsonify(items=[])
    h = db.load_history(_conn(), _workspace_user(ws))
    return jsonify(items=service.missing_dicts(_conn(), h.present, _now(), _ids()))


def _api_import(ws: str):
    """{"ids": ["<preset item id>", ...]} -> the new view."""
    ids = (request.get_json(silent=True) or {}).get("ids")
    if ws == "preset" or not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        return jsonify(error="bad import request"), 400
    try:
        h = service.mutate(_conn(), _workspace_user(ws), service.import_step(_conn(), ids, _now(), _ids()))
    except CommandError as e:
        return jsonify(error=str(e)), 400
    return jsonify(_view_for(ws, h))


def _api_preview():
    """{"recurrence": {...}} -> {"next_reset_at": ISO | null}. Read-only; for the task dialog."""
    try:
        rec = recurrence_from_dict((request.get_json(silent=True) or {}).get("recurrence"))
    except CodecError as e:
        return jsonify(error=str(e)), 400
    return jsonify(next_reset_at=dt_to_str(next_reset(rec, _now())))


def _admin_api_required(view):
    @auth.api_login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not _is_admin():
            return jsonify(error="admins only"), 403
        return view(*args, **kwargs)
    return wrapper


def _register_api(prefix: str, ws: str, guard) -> None:
    def api_view():
        return jsonify(_view_for(ws))

    def api_command():
        return _api_command(ws)

    def api_history(which: str):
        return _api_history(ws, which)

    def api_suggested():
        return _api_suggested(ws)

    def api_preview():
        return _api_preview()

    def api_missing():
        return _api_missing(ws)

    def api_import():
        return _api_import(ws)

    for rule, fn, methods in (
        ("api/view", api_view, ["GET"]),
        ("api/command", api_command, ["POST"]),
        ("api/<any(undo, redo):which>", api_history, ["POST"]),
        ("api/suggested", api_suggested, ["POST"]),
        ("api/preview", api_preview, ["POST"]),
        ("api/missing", api_missing, ["GET"]),
        ("api/import", api_import, ["POST"]),
    ):
        todo_bp.add_url_rule(prefix + rule, f"{ws}_{fn.__name__}", guard(fn), methods=methods)


_register_api("/", "me", auth.api_login_required)
_register_api("/robot/", "robot", lambda fn: fn)  # public on purpose: robot is a throwaway account
_register_api("/admin/", "preset", _admin_api_required)


# --- robot debug views (plain text / JSON, ?now=<ISO datetime> previews another moment) -----

def _robot_debug_view():
    """(view, None) or (None, error)."""
    try:
        now = dt_from_str(request.args["now"]) if "now" in request.args else None
    except CodecError as e:
        return None, str(e)
    return _view_for("robot", now=now), None


@todo_bp.get("/debug/robot")
def debug_robot_text():
    view, error = _robot_debug_view()
    body, status = (error, 400) if error else (render_text(view) + "\n", 200)
    return body, status, {"Content-Type": "text/plain; charset=utf-8"}


@todo_bp.get("/debug/robot.json")
def debug_robot_json():
    view, error = _robot_debug_view()
    return (jsonify(error=error), 400) if error else jsonify(view)
