import hmac
import secrets
from functools import wraps

from flask import jsonify, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

# Used when the username does not exist, so login takes the same time either way.
_DUMMY_HASH = generate_password_hash("not-a-real-password")


def hash_password(password: str) -> str:
    return generate_password_hash(password)


def verify_password(pw_hash: str | None, password: str) -> bool:
    return check_password_hash(pw_hash or _DUMMY_HASH, password) and pw_hash is not None


def login(user_id: int) -> None:
    session.clear()
    session["user_id"] = user_id
    session.permanent = True
    csrf_token()


def logout() -> None:
    session.clear()


def current_user_id() -> int | None:
    return session.get("user_id")


def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def csrf_ok(token: str | None) -> bool:
    return bool(token) and "csrf" in session and hmac.compare_digest(token, session["csrf"])


def page_login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return redirect(url_for("todo.login_page"))
        return view(*args, **kwargs)
    return wrapper


def api_login_required(view):
    """Requires a session, and for writes a JSON body plus a matching X-CSRF-Token header."""
    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_user_id() is None:
            return jsonify(error="not logged in"), 401
        if request.method != "GET" and not csrf_ok(request.headers.get("X-CSRF-Token")):
            return jsonify(error="bad csrf token"), 403
        return view(*args, **kwargs)
    return wrapper
