import re

USERNAME_RE = re.compile(r"^[a-z0-9_.-]{3,32}$")
PASSWORD_MIN = 8
PASSWORD_MAX = 256
ROBOT_USERNAME = "robot"    # public debug account; see /robot/
PRESET_USERNAME = "preset"  # holds the admin-managed preset; see /admin/
RESERVED_USERNAMES = frozenset({ROBOT_USERNAME, PRESET_USERNAME})


def normalize_username(username: str) -> str:
    return username.strip().lower()


def signup_errors(username: str, password: str, confirm: str) -> list[str]:
    errors = []
    if not USERNAME_RE.match(normalize_username(username)):
        errors.append("Username must be 3–32 characters: letters, digits, _ . -")
    elif normalize_username(username) in RESERVED_USERNAMES:
        errors.append("That username is reserved.")
    if len(password) < PASSWORD_MIN:
        errors.append(f"Password must be at least {PASSWORD_MIN} characters.")
    if len(password) > PASSWORD_MAX:
        errors.append(f"Password must be at most {PASSWORD_MAX} characters.")
    if password != confirm:
        errors.append("Passwords do not match.")
    return errors
