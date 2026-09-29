"""Plain-text rendering of the view model, for the debug CLI. Pure."""

from datetime import datetime
from typing import Any


def _until(iso: str | None, now: datetime) -> str:
    if iso is None:
        return ""
    secs = max(0, int((datetime.fromisoformat(iso) - now).total_seconds()))
    h, rem = divmod(secs, 3600)
    return f"{h}h {rem // 60:02d}m"


def _task_line(t: dict[str, Any], now: datetime, indent: str) -> list[str]:
    box = "[x]" if t["done"] else "[ ]"
    notes = []
    if t["subtasks"]:
        p = t["subtask_progress"]
        notes.append(f"{p['done']}/{p['total']}")
    else:
        notes.append(t["recurrence_label"])
    if t["counter"]:
        notes.append(f"{t['progress']}/{t['target']} ({t['counter']})")
    if not t["enabled"]:
        notes.append("opted out")
    if not t["active"]:
        notes.append("closed" if t["window"] else "not today")
    if t["fresh"]:
        notes.append("AVAILABLE AGAIN")
    if t["resets_at"]:
        notes.append(f"resets in {_until(t['resets_at'], now)}")
    if t["window"] and not t["done"]:
        w = t["window"]
        notes.append(f"closes in {_until(w['closes_at'], now)}" if w["open"] else f"opens in {_until(w['opens_at'], now)}")
    if t["timer"]:
        notes.append(f"timer {_until(t['timer']['ends_at'], now)}")
    if t["reminder_at"]:
        notes.append("REMINDER DUE" if t["reminder_due"] else f"reminder {t['reminder_at']}")
    if t["categories"]:
        notes.append(" ".join("#" + c for c in t["categories"]))
    lines = [f"{indent}{box} {t['title']}  ({'; '.join(notes)})  id={t['id']}"]
    for c in t["subtasks"]:
        lines += _task_line(c, now, indent + "    ")
    return lines


def render_text(view: dict[str, Any]) -> str:
    now = datetime.fromisoformat(view["now"])
    lines = [
        f"now {view['now']}  ·  next reset ({view['reset_label']}) in {_until(view['next_reset_at'], now)}",
        f"undo={'yes' if view['can_undo'] else 'no'}  redo={'yes' if view['can_redo'] else 'no'}",
    ]
    for s in view.get("suggested", []):
        lines.append(f"suggested: {s['title']} ({s['bay_name']}; {s['steps']} steps)  id={s['id']}")
    for i, b in enumerate(view["bays"], start=1):
        p = b["progress"]
        lines += ["", f"Bay {i} · {b['name']}  ({p['done']}/{p['total']})  id={b['id']}"]
        for t in b["tasks"] + b["opted_out"]:
            lines += _task_line(t, now, "  ")
    lines += ["", f"Timers  ({view['countdowns_over']}/{len(view['countdowns'])} over)"]
    for c in view["countdowns"]:
        state = "[OVER]" if c["over"] else f"[{_until(c['ends_at'], now)}]"
        lines.append(f"  {state} {c['title']}  (set for {c['seconds'] // 60} min)  id={c['id']}")
    lines += ["", f"Cooldowns  ({view['cooldowns_full']}/{len(view['cooldowns'])} full)"]
    for c in view["cooldowns"]:
        box = f"[{c['level']}/{c['capacity']}]"
        notes = [c["label"]]
        if not c["full"]:
            notes.append(f"ready in {_until(c['next_at'], now)}" if c["capacity"] == 1 else
                         f"+1 in {_until(c['next_at'], now)}, full in {_until(c['full_at'], now)}")
        lines.append(f"  {box} {c['title']}  ({'; '.join(notes)})  id={c['id']}")
    return "\n".join(lines)
