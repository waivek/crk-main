// Pure client-side rules: no DOM, no network, no storage, no clock (`now` is passed in).
// app.js reads inputs and renders; the decisions it needs live here so `node --test` can check
// them (tools/todo/tests/logic.test.js). Loaded as a plain script (window.TodoLogic) and by Node.
(function (exports) {
  "use strict";

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

  // A task anywhere in the view's bays (top level, steps, opted out), or null.
  function findTask(bays, id) {
    const search = (tasks) => {
      for (const t of tasks) {
        if (t.id === id) return t;
        const found = search(t.subtasks);
        if (found) return found;
      }
      return null;
    };
    for (const b of bays) {
      const found = search(b.tasks.concat(b.opted_out));
      if (found) return found;
    }
    return null;
  }

  // --- time ----------------------------------------------------------------------------

  function fmtDuration(ms) {
    let s = Math.ceil(ms / 1000);
    const d = Math.floor(s / 86400); s %= 86400;
    const h = Math.floor(s / 3600); s %= 3600;
    const m = Math.floor(s / 60); s %= 60;
    const pad = (n) => String(n).padStart(2, "0");
    if (d) return `${d}d ${h}h ${pad(m)}m ${pad(s)}s`;
    if (h) return `${h}h ${pad(m)}m ${pad(s)}s`;
    if (m) return `${m}m ${pad(s)}s`;
    return `${s}s`;
  }

  // A length as typed or copied from the game. Returns seconds, or null if it can't be read.
  //   "10" (minutes) · "10h 37m", "10h37m", "2d 3h", "1h 5m 30s", "90s" · "1:30" (h:mm) · "10:37:00" (h:mm:ss)
  function parseDuration(text) {
    const s = (text || "").trim().toLowerCase();
    let m;
    if ((m = s.match(/^(\d+)$/))) return +m[1] * 60;
    if ((m = s.match(/^(\d+):([0-5]\d)$/))) return +m[1] * 3600 + +m[2] * 60;
    if ((m = s.match(/^(\d+):([0-5]\d):([0-5]\d)$/))) return +m[1] * 3600 + +m[2] * 60 + +m[3];
    m = s.match(/^(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m(?:in)?)?\s*(?:(\d+)\s*s)?$/);
    if (!m || !(m[1] || m[2] || m[3] || m[4])) return null;
    return (+m[1] || 0) * 86400 + (+m[2] || 0) * 3600 + (+m[3] || 0) * 60 + (+m[4] || 0);
  }

  // Seconds as a length parseDuration reads back: "10h 37m", "2d 3h", "45s". Rounded up to the
  // minute once it's a minute or more.
  function fmtTimeLeft(seconds) {
    if (seconds < 60) return `${Math.max(0, Math.ceil(seconds))}s`;
    const total = Math.ceil(seconds / 60);
    const d = Math.floor(total / 1440);
    const h = Math.floor((total % 1440) / 60);
    const m = total % 60;
    return [d && `${d}d`, h && `${h}h`, m && `${m}m`].filter(Boolean).join(" ");
  }

  // "+30" / "30m" / "2h" from `now`, or "18:30" local time (today, else tomorrow). Returns Date or null.
  function parseWhen(text, now) {
    const s = (text || "").trim();
    let m;
    if ((m = s.match(/^(\d{1,2}):(\d{2})$/))) {
      const d = new Date(now);
      d.setHours(+m[1], +m[2], 0, 0);
      if (d <= now) d.setDate(d.getDate() + 1);
      return d;
    }
    const secs = parseDuration(s.replace(/^\+/, ""));
    return secs ? new Date(now.getTime() + secs * 1000) : null;
  }

  // How far a cooldown's refill bar has filled (0..1): `spanMs` long, ending at `untilMs`.
  const refillFraction = (untilMs, spanMs, nowMs) => Math.min(1, Math.max(0, 1 - (untilMs - nowMs) / spanMs));

  // --- filters -------------------------------------------------------------------------
  // Filter state: { filter: the one tag to show only (or null), hidden: tags to leave out }.

  function tagState(state, tag) {
    if (state.hidden.includes(tag)) return "hidden";
    return state.filter === tag ? "only" : "shown";
  }

  const NO_FILTERS = Object.freeze({ filter: null, hidden: Object.freeze([]) });

  // A tag's name (filter bar or task row): toggles "only this tag" (and un-hides it).
  function toggleOnlyTag(state, tag) {
    const filter = tag && state.filter !== tag ? tag : null;
    return { filter, hidden: filter ? state.hidden.filter((t) => t !== filter) : state.hidden };
  }

  // A tag's eye button: hides it, or shows it again. Hiding the "only" tag drops that filter too.
  function toggleHideTag(state, tag) {
    if (state.hidden.includes(tag)) return { ...state, hidden: state.hidden.filter((t) => t !== tag) };
    return { filter: state.filter === tag ? null : state.filter, hidden: [...state.hidden, tag] };
  }

  // The line under the filter bar: "Only #Weekly · Hiding #Ads, #Events". Null when nothing is
  // filtered. Hidden tags that no longer exist (not in `categories`) hide nothing, so aren't listed.
  function filterSummary(state, categories) {
    const hidden = state.hidden.filter((t) => categories.includes(t));
    const parts = [
      state.filter ? `Only #${state.filter}` : "",
      hidden.length ? `Hiding ${hidden.map((t) => "#" + t).join(", ")}` : "",
    ].filter(Boolean);
    return parts.length ? parts.join(" · ") : null;
  }

  // The query the server narrows the view with (core/view.py filter_view).
  function viewQuery({ filter, hidden, hideDone }) {
    const q = new URLSearchParams();
    for (const t of hidden) q.append("hide_tag", t);
    if (filter) q.set("category", filter);
    if (hideDone) q.set("hide_done", "1");
    const s = q.toString();
    return s ? "?" + s : "";
  }

  // What the filters left out of a bay: "All done · 3 done hidden · 2 hidden by tag". Null if nothing.
  function hiddenNote(bay) {
    const parts = [
      bay.hidden_done ? `${bay.hidden_done} done hidden` : "",
      bay.hidden_tagged ? `${bay.hidden_tagged} hidden by tag` : "",
    ].filter(Boolean);
    if (!parts.length) return null;
    const allDone = bay.hidden_done && !bay.hidden_tagged && !bay.tasks.length;
    return { icon: bay.hidden_done ? "done_all" : "visibility_off", text: (allDone ? "All done · " : "") + parts.join(" · ") };
  }

  // --- task dialog ---------------------------------------------------------------------

  const splitTags = (text) => (text || "").split(",").map((s) => s.trim()).filter(Boolean);

  // Blank or 1 is a plain checkbox (stored as 0); 2 or more is a counter.
  function counterTarget(value) {
    const n = Math.floor(+value || 0);
    return n >= 2 ? n : 0;
  }

  // What's set under the collapsed "Counter & tags", e.g. "· ×5 · #ads".
  function moreSummary(target, tagsText) {
    const n = counterTarget(target);
    const parts = [n ? `×${n}` : "", ...splitTags(tagsText).map((c) => "#" + c)].filter(Boolean);
    return parts.length ? "· " + parts.join(" · ") : "";
  }

  // Daily windows are minutes after the daily reset, shown as HH:MM. An end of 00:00 is midnight
  // at the end of the day (24:00).
  const DAY_MIN = 24 * 60;
  const minutesToHhmm = (m) => `${String(Math.floor((m % DAY_MIN) / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  function hhmmToMinutes(s) {
    const m = /^(\d{2}):(\d{2})$/.exec(s || "");
    return m ? +m[1] * 60 + +m[2] : null;
  }
  function windowRange(startText, endText) {
    const start = hhmmToMinutes(startText);
    const end = hhmmToMinutes(endText);
    return start === null || end === null ? null : [start, end === 0 ? DAY_MIN : end];
  }

  // The row "Add window" adds: it repeats the pattern so far (the gap between the last two
  // openings, or with one row, starting where it ends) with the last row's length. The first row is
  // 00:00–01:00. Null when it would pass midnight. `rows` are [start, end]; unreadable rows are skipped.
  function nextWindow(rows) {
    rows = rows.filter(Boolean);
    const last = rows[rows.length - 1];
    if (!last) return [0, 60];
    const length = last[1] - last[0];
    const step = rows.length >= 2 ? last[0] - rows[rows.length - 2][0] : length;
    const start = last[0] + (step > 0 ? step : length);
    return start >= DAY_MIN ? null : [start, Math.min(start + length, DAY_MIN)];
  }

  // The repeat the dialog's fields describe. Throws an Error with a message for the user.
  // `windows` are [startText, endText] pairs from the rows; `days` are weekday numbers (0 = Mon).
  function recurrence({ kind, weekday, days = [], windows = [], every, anchor }) {
    switch (kind) {
      case "weekly":
        return days.length && days.length < 7 ? { kind, weekday: +weekday, days } : { kind, weekday: +weekday };
      case "days_of_week":
        if (!days.length) throw new Error("Pick at least one day.");
        return { kind, days };
      case "windows": {
        const ranges = windows.map(([s, e]) => windowRange(s, e));
        if (ranges.includes(null)) throw new Error("Fill in every window's times (or remove the row).");
        if (!ranges.length) throw new Error("Add at least one window.");
        return { kind, windows: ranges };
      }
      case "interval": {
        const n = Math.floor(+every || 0);
        if (n < 2) throw new Error("Every N days needs N of at least 2.");
        if (!anchor) throw new Error("Pick a day it resets.");
        return { kind, every: n, anchor };
      }
      default:
        return { kind };
    }
  }

  // --- timers --------------------------------------------------------------------------

  const MAX_HOURS = 23; // the time-left fields: up to 23h 59m (the server allows longer)

  // The add/edit_countdown fields from a name and the time left as hours (0–23) + minutes (0–59)
  // fields; blank counts as 0. Throws an Error with a message for the user.
  function timerFields(title, hours, minutes) {
    const t = (title || "").trim();
    if (!t) throw new Error("Name the timer, e.g. Mine Venture.");
    const h = hours === "" || hours == null ? 0 : Number(hours);
    const m = minutes === "" || minutes == null ? 0 : Number(minutes);
    if (!Number.isInteger(h) || !Number.isInteger(m) || h < 0 || m < 0) throw new Error("Hours and minutes must be whole numbers.");
    if (h > MAX_HOURS) throw new Error(`Hours can be at most ${MAX_HOURS}.`);
    if (m > 59) throw new Error("Minutes can be at most 59.");
    const seconds = h * 3600 + m * 60;
    if (!seconds) throw new Error("Enter the time left: hours and/or minutes.");
    return { title: t, seconds };
  }

  // Seconds as the hours + minutes fields show them, rounded up to the minute (10h 36m 20s → 10, 37).
  function splitTimeLeft(seconds) {
    const total = Math.max(0, Math.ceil(seconds / 60));
    return { hours: Math.floor(total / 60), minutes: total % 60 };
  }

  // --- cooldown dialog -----------------------------------------------------------------

  // The add/edit_cooldown fields from the dialog's inputs. Throws an Error with a message for the
  // user; the core checks everything again.
  function cooldownFields({ title, hours, minutes, capacity, current, progressKind, progressHours, progressMinutes }) {
    const refill = (+hours || 0) * 60 + (+minutes || 0);
    if (refill <= 0) throw new Error("The refill time must be longer than 0 minutes.");
    const fields = {
      title,
      minutes: refill,
      capacity: Math.floor(+capacity || 0),
      value: Math.floor(+current || 0),
    };
    if (progressKind) {
      const progress = (+progressHours || 0) * 60 + (+progressMinutes || 0);
      if (progress >= refill) throw new Error("The time left or passed must be less than the refill time.");
      fields.progress = { [progressKind]: progress };
    }
    return fields;
  }

  Object.assign(exports, {
    esc, findTask,
    fmtDuration, parseDuration, fmtTimeLeft, parseWhen, refillFraction, timerFields, splitTimeLeft,
    NO_FILTERS, tagState, toggleOnlyTag, toggleHideTag, filterSummary, viewQuery, hiddenNote,
    splitTags, counterTarget, moreSummary,
    DAY_MIN, minutesToHhmm, hhmmToMinutes, windowRange, nextWindow, recurrence,
    cooldownFields,
  });
})(typeof module === "object" && module.exports ? module.exports : (window.TodoLogic = {}));
