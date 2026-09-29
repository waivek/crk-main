# Todo tool — session handoff

A multi-user daily-task tracker for **Cookie Run: Kingdom**. It is served by the existing Flask/gunicorn app in `crk-main-api.py` under **`/tools/todo/`**.
Live at https://crk.stardews.com/tools/todo/

## How to work on it (the user's preferences)

- **Architecture:** Gary Bernhardt's *functional core / imperative shell*.
  - All logic lives in `core/`. It is pure: no I/O, no clock, no randomness, and `now` is always passed in. Each rule is testable without the UI.
  - The shell (`shell/`, templates, static files) only does I/O. Don't couple logic with the view.
  - New rules belong in `core/` together with tests. The JS only renders the server's view model and sends commands.
- **Python:** use `uv` for everything (`uv run …`, `uv add …`). Never use pip or `.venv/bin/python`.
- **Screenshots:** don't try to take any. Headless browsers don't work on this box, and the user asked us to stop. The user reports visual issues themselves. To inspect state, use the robot account or the CLI (below).
- **UI style:**
  - Spartan: black background, white text, no gradients.
  - Inter font; Google Material Symbols for icons.
  - Modern CSS: `@layer`, nesting, `:has()`, container queries, `color-mix()`, View Transitions.
  - jQuery client plus Jinja templates plus a JSON API.
- **Pronouns:** use they/them for the user.
- **Communication:** say plainly what was and wasn't verified, especially the UI, which we can't see.

## Run, test, deploy

```bash
uv run pytest tools/todo/tests -q          # 215 tests, ~4s (run from the repo root)
kill -HUP <gunicorn master pid>            # reload after Python/template changes
ps -eo pid,args | grep "gunicorn.*crk-main-api" | grep -v grep   # master = the python3 line whose parent is `uv run`
```

- **Server:** gunicorn runs on `127.0.0.1:5183` with 1 worker, started by the user **without `--reload`**. At the time of writing the master pid was 2141410. After changing Python or templates, send it a HUP.
- **Static files:** CSS/JS changes need no reload. The asset URLs carry `?v=<mtime>` (see `_asset_version` in `shell/web.py`) because Cloudflare/browsers cache for 4 hours.
- **Database:** SQLite at `tools/todo/data/todo.sqlite3` (gitignored via `data/`). The session secret is in `data/secret_key`.
- **Migrations:** `shell/db.py` `MIGRATIONS` is **append-only** and indexed by `PRAGMA user_version` (currently 10). It runs automatically on the first request after a reload.
- **Git:** nothing under `tools/` is committed yet (untracked). There are also unrelated pending changes in the repo. Ask before committing.

## Debug access (no login needed)

| What | How |
|---|---|
| Public robot account (full normal UI) | https://crk.stardews.com/tools/todo/robot/ |
| Robot's state as text / JSON | `/tools/todo/debug/robot`, `/tools/todo/debug/robot.json` (`?now=2026-09-25T15:00:00Z` time-travels) |
| Drive robot over HTTP | `POST /tools/todo/robot/api/command` with `{"type": ...}`; also `/robot/api/undo`, `/redo`, `/suggested`, `/preview`, `/import`; `GET /robot/api/view` (`?category=…&hide_done=1`), `/missing` |
| Any user, straight from SQLite | `uv run python -m tools.todo.debug show vivek` · `cmd vivek '<json>'` · `undo`/`redo` · `users` · `make-admin`/`revoke-admin` · `show preset` · `--now`, `--json` |

The reserved usernames are `robot` and `preset`; signup rejects both. The users are `vivek` (admin), `robot` and `preset`.
When you test on robot, **undo afterwards** so the user sees it unchanged.

## Layout

```
crk-main-api.py            registers todo_bp at /tools/todo
tools/todo/
  core/        PURE
    model.py       Task, Bay, Cooldown, State, Recurrence, Timer (frozen dataclasses)
    schedule.py    resets (KST), is_done, is_active, item_done/active (groups), progress, next_reset,
                   cooldown level/is_full/next_refill_at/full_at
    commands.py    command dataclasses + apply(state, cmd, now) -> (state, events)
    ordering.py    move/renumber tasks & bays
    history.py     undo/redo stack (100 steps)
    preset.py      admin preset: instantiate for new users, missing() / suggested(), import_items(), hide()
    view.py        build_view(state, now) -> JSON dict; filter_view(view, tag, hide_done);
                   missing_view (Browse list); labels (recurrence, cooldown, windows)
    codec.py       dict <-> model, validated parse_command(payload, fresh_id); upgrades old
                   "cooldown" tasks into Cooldowns on load (_adopt_legacy_cooldowns)
    text_view.py   plain-text rendering (debug)
    defaults.py    built-in starter template (only seeds the preset the first time)
    auth_rules.py  signup validation, reserved names
    config.py      RESET_TZ=Asia/Seoul, DAILY_RESET=00:00, HISTORY_LIMIT
  shell/
    web.py         blueprint; "workspaces": me (/), robot (/robot/), preset (/admin/, admins only)
    service.py     mutate(), create_user (copy of preset), suggested import/hide, Browse import_step
    db.py          sqlite, migrations, load/save state + history snapshots
    auth.py        werkzeug hashing, session, CSRF (X-CSRF-Token header for API)
  debug.py       CLI
  templates/todo/  app.html (single page), login/signup
  static/          app.js (jQuery renderer), app.css
  tests/           pytest; conftest pins reset to UTC midnight (KST tests opt in). One file per
                   feature: test_cooldowns, test_windows, test_browse, test_duplicate, …
```

The request flow: load history → pure step (`apply` / `undo` / `import_item` …) → save → `build_view` → JSON.
The state is saved as whole-user rows plus JSON snapshots for undo.

## Features as they stand

- **Accounts:** multi-user login/signup. Admins get an `admin_panel_settings` icon linking to the preset editor.
- **Bays** (shown as "Bay N") hold ordered tasks. Tasks can be reordered (up/down/top/bottom), and bays can be moved.
- **Subtasks:** one level deep. The view sends `is_group`, and the JS must use it rather than
  `subtasks.length`, because filters can show a group with none of its steps.
  - Ticking a parent ticks today's active steps.
  - A parent is done when today's steps are done.
  - Finished groups start **collapsed**, with a chevron to open them.
- **Repeats:**
  - once
  - daily
  - weekly (reset weekday, plus optional active days, e.g. a tally day)
  - on specific days
  - every N days (with an anchor date and ±1-day buttons)
  - **at set times** (`kind: "windows"`): daily time windows entered as rows in the dialog, e.g.
    Monster Menace 00:00–12:00 and 12:00–24:00, or the Post Office 00:00–00:30, 02:00–02:30, …
    - It can be claimed once per window and is closed in between. Closed works like "not today":
      it can't be ticked or counted and sorts lower, and `inactive_label` says "Closed".
    - Missed windows don't carry over.
    - Windows are stored as `(start, end)` minutes after the daily reset (KST). The codec sorts them
      and rejects overlaps and anything crossing midnight; 24:00 is written as an end of 00:00.
    - The view's `window` gives `open`, `closes_at`, `opens_at` and `times`. "Add window" in the
      dialog repeats the gap between the last two rows.
- **Cooldowns tab** (`#cooldowns` in the URL): things that refill on their own timer and are
  emptied by claiming them.
  - A `Cooldown` refills 1 every `minutes`, up to `capacity`. A plain cooldown such as the Fountain or
    Harbour Ship (claim, then wait 8h) is simply `capacity` 1. The level is derived from `emptied_at`
    (None = full); nothing is stored per unit.
  - Anything can be claimed at any time, which sets it to 0 and restarts refilling. There is no
    "early" flag anymore.
  - **Current value** (`value` in `add_cooldown` / `edit_cooldown`): how many it holds right now,
    0..capacity, with a default of 0 when adding. It isn't stored as a field. It's applied as
    `emptied_at = now - value * minutes` (None when full; see `schedule.emptied_at_for`), so the next
    one refills a full cycle later. Re-saving the value it already has (the edit dialog always sends
    it) keeps the refill progress.
  - **Next one (optional)** (`progress` in add/edit: `{"remaining": min}` or `{"elapsed": min}`,
    i.e. `RefillProgress`): how far the next refill has got, copied from the game. Time left must
    be > 0 and time passed >= 0, and both must be less than the refill time. It can't be set while
    full. It combines with the current value, which defaults to the current level on edit.
  - Preset copies copy `emptied_at` verbatim, so a new user's copy holds what the preset's cooldown
    holds at that moment. The admin controls a copy's starting value by setting the preset's current value.
  - They are their own type in `State.cooldowns`, not tasks, and don't belong to any bay.
    "cooldown" is no longer a repeat kind, so tasks and subtasks can't use it.
  - Commands: `add_cooldown` (title, minutes, capacity, value), `edit_cooldown`,
    `remove_cooldown`, `move_cooldown`, `claim`, `unclaim` (back to full). All are keyed by `cooldown_id`.
  - The list is sorted soonest-ready first: full ones, then by `full_at` (not `next_at`, even for
    refilling ones). Stored order (the move buttons) only breaks ties, e.g. among the full ones.
  - **Rows:** no strikethrough. Waiting is "ongoing", so a cooldown that isn't full gets a thin
    `.refill` bar that fills toward the next one, kept moving by `tick()`. Its initial width is
    rendered inline so re-renders don't animate from 0. Claim buttons read Claim / Claim N / Claim
    early (at 0; it restarts the wait).
  - The preset has cooldowns too. New users get copies, and new preset cooldowns show up under
    Suggested ("· Cooldowns").
  - Migration (2026-09-25): the old cooldown tasks became cooldowns with the same ids and claim times.
    Migration 8 then added capacity, renamed claimed_at to emptied_at and dropped `early`. Migration 9
    replaced start_empty with `start`, and migration 10 dropped `start` in favour of the current value.
    The codec still reads the older snapshot fields.
    The "Claims" bays they emptied were removed. A backup from before is at
    `data/todo.before-cooldowns.sqlite3`. Old undo snapshots are upgraded by the codec when loaded.
- **Task dialog:** Title, then Repeats (Daily by default) with its own fields and the live
  preview, then a collapsed **"Counter & tags"** section. The counter is optional: blank or 1
  means a plain checkbox (stored `target` 0). The section opens itself when editing a task that
  has a counter or tags, or when adding while a #tag filter is active (the tag is pre-filled).
  Its summary shows what's set, e.g. "· ×5 · #ads".
- **Resets:** all resets happen at **00:00 KST**. "Done" is derived from `completed_at` versus the last reset; there is no cron job.
- **Not today:** tasks outside their active days can't be checked, counted or claimed. Unticking is still allowed.
- **Counters:** `target >= 2`. Up to 10 shows dots, more shows a slider. Examples: Normal Ads 5, Tree of Wishes 45. Counters reset with the task.
- **Duplicate** (row menu, `duplicate_task`): puts a fresh copy titled "… (copy)" right below the
  original, among the same siblings. A subtask stays under its parent, and a group is copied with all
  its steps (step ids are `<new id>-<n>`). The copy keeps the repeat, count target, tags and opt-out,
  but has no completion, progress, timer, reminder or preset origin.
- **Other task features:**
  - timers
  - reminders (badge only; there are `ReminderDue` events for a future notifier)
  - opt-out
  - #tag categories with a server-side filter
  - **Hide done**: a toggle chip in the filter bar (`?hide_done=1`, handled by `filter_view` in
    the core). It hides finished tasks, and finished steps of unfinished groups. Bays stay, with
    `hidden_done` giving a count for "All done · N done hidden". It can be combined with a #tag. The
    setting is remembered per browser in localStorage (`todo.hideDone`), and "Not today"/closed
    tasks stay visible.
  - undo/redo (Ctrl+Z / Ctrl+Shift+Z)
- **Admin preset** (`/tools/todo/admin/`):
  - New users start with a copy of it.
  - Top-level items added later show up for existing users under **Suggested**, with Import / Hide. Copies track `origin_id`.
  - Preset edits never change users' existing copies.
  - **Browse** (the header's `library_add` button; not shown on the admin page): lists everything
    in `preset.missing()`, grouped by destination bay or Cooldowns, and imports the ticked items in
    one undoable step via `POST api/import {"ids": [...]}`. The list comes from `GET api/missing`.
    - It includes items hidden from Suggested, items the user deleted, and **steps the admin added
      to groups the user already has**. A step goes into the user's copy of that group, found by
      `origin_id`, even if the group was renamed or moved.
    - Suggested is `missing()` minus steps and hidden items.
- **Dialog:** a live "Resets in …" preview. It is computed by the server (`/api/preview` → `schedule.next_reset`).
- **UI polish:**
  - an optimistic spinner while a request is pending
  - View Transitions that slide finished tasks to the bottom
  - a live local clock and a countdown to the next KST reset
  - **Countdowns** (cooldowns, windows, "Resets", timers "Ends", the header reset): hovering shows
    the full local date and time with the browser's zone. Tapping any of them switches all of them
    between "in 2h 05m" and "at 20:35" (local), remembered in localStorage (`todo.clockTimes`). Each
    `.countdown` prints its own "in"/"at", so labels around it must not say "in" (e.g. "Ready ",
    "Closes "). `data-fixed` opts out; the dialog preview uses it.
- **Removed on purpose:** event end dates, and linked tasks (subtasks replace them). Old undo snapshots may still contain those keys; the codec ignores them.

## Gotchas learned the hard way

- **Caching:** assets are cached for 4h, so always keep the `?v=` busting. Stale CSS once made rows stack vertically.
- **Narrow screens:** below 30rem, `.hide-narrow` bay buttons fold into a bay ⋮ menu (`.show-narrow`).
  Anything hidden this way needs a menu equivalent, or phones lose it.
- **Menus:** every row and bay has a `view-transition-name`, which makes each one a stacking context. Open kebab menus rely on `.bay:has(.menu[open])` and `.task:has(.menu[open])` getting `z-index`. Don't put `overflow: clip` on `.bay`.
- **Form CSS:** `.stack label` out-specifies plain classes. The dialog's show/hide rules are scoped under `#task-form` / `#cooldown-form`.
- **Hidden form fields:** don't put `required` or restrictive `min` on inputs that can be hidden
  (collapsed `<details>`, other repeat kinds' fieldsets). The browser then blocks submit without
  saying anything. Validate in JS, and in the core.
- **Filters stack:** `filter_view` applies the #tag filter, then hide-done. When changing either,
  test them combined (e.g. a group narrowed to one tagged step that is then done).
- **Transactions:** `service.mutate` opens `BEGIN IMMEDIATE`. Read the preset **before** calling it; nested transactions fail.
- **Time zones:** date math for game days uses the reset timezone (`RESET_TZ`). The tests pin UTC through `conftest.py`.

## Open items / ideas not done

- **Placeholder names:** "Starlight Island 1/2/3" are placeholders; the real names were never given. They can be renamed in the admin preset.
- **Notifications:** nothing alerts the user when a timer, reminder, window or cooldown comes due;
  only the page's chips change. The core already emits `ReminderDue` / `Completed` / `Claimed`
  events. Proposed but not built:
  - browser notifications and sound while the tab is open;
  - web push or Telegram for when it's closed;
  - an **"Expires in / at"** field in the Add task dialog, for one-off timers like "Mining Bell
    expires in 2:15". Today that's a Once task plus ⋮ → Start timer, which takes two steps.
- **Quick add:** a per-bay "+ Add a daily task…" inline input (Enter adds a Daily checkbox) was
  proposed and liked, but not built. Only the dialog improvements were done.
- **Cooldowns:** Duplicate exists for tasks only. The up/down buttons on waiting cooldowns change
  nothing visible, because they only break ties; hiding them was offered.
- **Windows:** a window can't cross midnight (split it into two rows). For windows of varying length
  (the Arcade is sometimes 4h, sometimes 5h), the advice was to enter the longest.
- **Preset edits:** they don't propagate to users who already imported an item. This was an explicit user choice (Suggested covers new items only).
- **Robot:** the robot account is public and anyone can edit it. A token could be added if that ever matters.
- **Login:** there is no rate limiting on login.
