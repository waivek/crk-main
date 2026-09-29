// Thin client: renders the server's view model and sends commands. No business logic here; the
// client-side rules it needs (parsing, filter state, form checks) are pure functions in logic.js.
$(function () {
  "use strict";
  const {
    esc, fmtDuration, fmtTimeLeft, parseWhen, refillFraction, timerFields, splitTimeLeft,
    NO_FILTERS, tagState, toggleOnlyTag, toggleHideTag, filterSummary, viewQuery: buildViewQuery, hiddenNote,
    splitTags, counterTarget, moreSummary, minutesToHhmm, windowRange, nextWindow, recurrence: buildRecurrence,
    cooldownFields,
  } = window.TodoLogic;

  const BASE = $('meta[name="todo-base"]').attr("content");
  const API = $('meta[name="todo-api"]').attr("content");
  const CSRF = $('meta[name="csrf-token"]').attr("content");
  const dialog = document.getElementById("task-dialog");
  const $form = $("#task-form");
  const cooldownDialog = document.getElementById("cooldown-dialog");
  const timerDialog = document.getElementById("timer-dialog");
  const browseDialog = document.getElementById("browse-dialog");
  const $cooldownForm = $("#cooldown-form");

  let view = null;
  let clockOffset = 0;   // server clock minus client clock, in ms
  // Tag filters: { filter: the one #tag to show only, hidden: #tags to leave out } (see logic.js).
  let filters = NO_FILTERS;
  // "Hide done": remembered per browser (a convenience; the page works without storage).
  const HIDE_DONE_KEY = "todo.hideDone";
  let hideDone = false;
  try { hideDone = localStorage.getItem(HIDE_DONE_KEY) === "1"; } catch { /* storage blocked */ }
  // Hidden #tags are remembered per browser too (the one "show only" tag isn't).
  const HIDDEN_TAGS_KEY = "todo.hiddenTags";
  try {
    const saved = JSON.parse(localStorage.getItem(HIDDEN_TAGS_KEY));
    if (Array.isArray(saved)) filters.hidden = saved.filter((t) => typeof t === "string");
  } catch { /* storage blocked or unreadable */ }
  function setFilters(next) {
    filters = next;
    try { localStorage.setItem(HIDDEN_TAGS_KEY, JSON.stringify(filters.hidden)); } catch { /* storage blocked */ }
    renderFilters(); // update the chips right away; the narrowed view follows
    refresh();
  }
  // Groups the user opened/closed by hand: id -> {open, done}. A choice only sticks while the
  // group's done state is unchanged, so finishing a group collapses it again.
  const groupChoice = new Map();
  let editing = null;    // {bayId, parentId} for add, {task} for edit
  let editingCooldown = null;  // the cooldown being edited, or null when adding one

  // --- helpers -----------------------------------------------------------------------

  const RECUR_ICONS = {
    once: "looks_one", daily: "today", weekly: "date_range",
    days_of_week: "calendar_view_week", interval: "event_repeat", windows: "schedule",
  };
  const icon = (name) => `<span class="material-symbols-outlined" aria-hidden="true">${name}</span>`;
  const iconBtn = (action, name, label, disabled = false, cls = "") =>
    `<button type="button" class="icon-btn ${cls}" data-action="${action}" title="${label}" aria-label="${label}"${disabled ? " disabled" : ""}>${icon(name)}</button>`;
  const menuItem = (action, name, label, cls = "") =>
    `<button type="button" class="${cls}" data-action="${action}">${icon(name)}${label}</button>`;

  const findTask = (id) => window.TodoLogic.findTask(view.bays, id);

  const fmtWhen = (iso) =>
    new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

  function toast(msg) {
    const $t = $("#toast").text(msg).prop("hidden", false);
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => $t.prop("hidden", true), 3000);
  }

  // --- server ------------------------------------------------------------------------

  const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");

  // `animate` uses the View Transitions API so rows glide to their new spots (e.g. done -> bottom).
  function setView(v, animate = false) {
    const apply = () => {
      view = v;
      clockOffset = Date.parse(v.now) - Date.now();
      render();
    };
    if (animate && document.startViewTransition && !reduceMotion.matches) document.startViewTransition(apply);
    else apply();
  }

  // Instant feedback while the server confirms: spinner on the control, optional optimistic done state.
  function markPending(control, taskEl, optimisticDone) {
    if (taskEl) {
      taskEl.classList.add("pending");
      if (optimisticDone !== undefined) $(taskEl).find(".task").addBack().toggleClass("done", optimisticDone);
    }
    if (control) {
      control.disabled = true;
      const ic = control.querySelector(".material-symbols-outlined");
      if (ic) ic.textContent = "progress_activity";
      control.classList.add("pending");
    }
  }

  function api(method, path, body) {
    return $.ajax({
      url: API + path + viewQuery(),
      method,
      contentType: "application/json",
      data: body === undefined ? undefined : JSON.stringify(body),
      headers: { "X-CSRF-Token": CSRF },
      dataType: "json",
    })
      .done((v) => setView(v, method !== "GET"))
      .fail((xhr) => {
        if (xhr.status === 401) return (location.href = BASE + "login");
        toast((xhr.responseJSON && xhr.responseJSON.error) || "Request failed");
        if (view) render(); // drop any optimistic / pending state
      });
  }

  // The server narrows the view (core/view.py filter_view): hidden #tags, one #tag, unfinished tasks.
  const viewQuery = () => buildViewQuery({ ...filters, hideDone });

  const send = (payload) => api("POST", "command", payload);
  const refresh = () => api("GET", "view");
  const undo = () => view.can_undo && api("POST", "undo", {});
  const redo = () => view.can_redo && api("POST", "redo", {});

  // --- render ------------------------------------------------------------------------

  function render() {
    $("#undo").prop("disabled", !view.can_undo);
    $("#redo").prop("disabled", !view.can_redo);
    renderSuggested();
    renderFilters();
    const empty = view.filter
      ? `<p class="empty">Nothing tagged #${esc(view.filter)}.</p>`
      : '<p class="empty">No bays yet. Add one above, e.g. “Daily” or “Town Square”.</p>';
    $("#bays").html(view.bays.map(renderBay).join("") || empty);
    renderCooldowns();
    $("#reset-label").text(view.reset_label);
    $("#reset-countdown").attr("data-until", view.next_reset_at).removeAttr("data-fired").parent().removeClass("ringing");
    tick();
  }

  // Preset items this user doesn't have yet (e.g. a new event): Import copies it in.
  function renderSuggested() {
    const list = view.suggested || [];
    $("#suggested").prop("hidden", !list.length).html(list.length ? `
      <h2>${icon("campaign")}Suggested <span class="badge">${list.length}</span></h2>
      <ul>${list.map((s) => `
        <li data-suggested="${esc(s.id)}">
          <span class="s-text">${esc(s.title)} <span class="muted">· ${esc(s.bay_name)}${s.steps ? ` · ${s.steps} step${s.steps === 1 ? "" : "s"}` : ""}</span></span>
          <button type="button" class="primary" data-suggested-action="import">${icon("download")}Import</button>
          <button type="button" class="icon-btn" data-suggested-action="hide" title="Hide" aria-label="Hide">${icon("close")}</button>
        </li>`).join("")}</ul>` : "");
  }

  // Filter bar: Hide done, All, one chip per tag, and a line saying what's filtered.
  function renderFilters() {
    const all = `<button type="button" class="chip" data-filters-clear aria-pressed="${!filters.filter && !filters.hidden.length}"
      title="Show every tag">All</button>`;
    // Each tag: its name toggles "only this tag"; the eye hides or shows it. Two buttons, one job each.
    const tagChip = (c) => {
      const state = tagState(filters, c);
      const name = state === "only" ? `Stop showing only #${c}` : `Show only #${c}`;
      const eye = state === "hidden" ? `Show #${c} again` : `Hide #${c}`;
      return `<span class="chip tag tag-split ${state}" role="group" aria-label="#${esc(c)}">
        <button type="button" class="tag-name" data-filter="${esc(c)}" aria-pressed="${state === "only"}" title="${esc(name)}">${icon("sell")}<span class="name">${esc(c)}</span></button>
        <button type="button" class="tag-eye" data-tag-hide="${esc(c)}" aria-pressed="${state === "hidden"}" title="${esc(eye)}" aria-label="${esc(eye)}">${icon(state === "hidden" ? "visibility_off" : "visibility")}</button>
      </span>`;
    };
    const summary = filterSummary(filters, view.categories);
    const status = summary
      ? `<p class="filter-status">${icon("filter_alt")}<span>${esc(summary)}</span><button type="button" class="link" data-filters-clear>Show all</button></p>`
      : "";
    const toggle = `<button type="button" class="chip hide-done" id="hide-done" aria-pressed="${hideDone}"
      title="Show only what's left to do">${icon(hideDone ? "check_box" : "check_box_outline_blank")}Hide done</button>`;
    const tags = view.categories.length
      ? `<span class="filter-sep" aria-hidden="true"></span>` + all + view.categories.map(tagChip).join("")
      : "";
    $("#filters").html(toggle + tags + status);
  }

  // The server already narrowed the view to the selected #tag (see core/view.py filter_view).
  // On narrow screens the bay's move/rename/remove buttons fold into a ⋮ menu (see app.css).
  function renderBay(b) {
    const first = b.position === 0;
    const last = b.position === view.bay_count - 1;
    const tasks = b.tasks;
    const off = b.opted_out;
    const fresh = tasks.filter((t) => t.fresh).length;
    return `
      <section class="bay" data-bay="${esc(b.id)}" style="view-transition-name: b-${esc(b.id)}">
        <header class="bay-head">
          <span class="bay-num">Bay ${b.position + 1}</span>
          <h2>${esc(b.name)}</h2>
          <span class="progress">${b.progress.done}/${b.progress.total}</span>
          ${fresh ? `<span class="chip fresh" title="Reset today after a longer wait">${icon("new_releases")}${fresh}<span class="label">&nbsp;available again</span></span>` : ""}
          <div class="bay-actions">
            ${iconBtn("bay-up", "arrow_upward", "Move bay up", first, "hide-narrow")}
            ${iconBtn("bay-down", "arrow_downward", "Move bay down", last, "hide-narrow")}
            ${iconBtn("bay-rename", "edit", "Rename bay", false, "hide-narrow")}
            ${iconBtn("bay-remove", "delete", "Remove bay", false, "hide-narrow")}
            ${iconBtn("task-new", "add", "Add task")}
            <details class="menu show-narrow">
              <summary aria-label="Bay actions">${icon("more_vert")}</summary>
              <div class="menu-items">
                <button type="button" data-action="bay-up"${first ? " disabled" : ""}>${icon("arrow_upward")}Move bay up</button>
                <button type="button" data-action="bay-down"${last ? " disabled" : ""}>${icon("arrow_downward")}Move bay down</button>
                ${menuItem("bay-rename", "edit", "Rename bay")}
                ${menuItem("bay-remove", "delete", "Remove bay", "danger")}
              </div>
            </details>
          </div>
        </header>
        <ul class="tasks">${tasks.map(renderTask).join("")}</ul>
        ${hiddenNoteHtml(b)}
        ${off.length ? `<details class="opted-out"><summary>Opted out (${off.length})</summary><ul class="tasks">${off.map(renderTask).join("")}</ul></details>` : ""}
      </section>`;
  }

  // What the filters left out of this bay: "All done · 3 done hidden · 2 hidden by tag".
  function hiddenNoteHtml(b) {
    const note = hiddenNote(b);
    return note ? `<p class="hidden-done">${icon(note.icon)}${esc(note.text)}</p>` : "";
  }

  function renderTask(t) {
    const isParent = t.is_group;
    const choice = groupChoice.get(t.id);
    const collapsed = choice && choice.done === t.done ? !choice.open : t.collapsed;
    const cls = ["task", t.done && "done", !t.active && "inactive", !t.enabled && "off", t.reminder_due && "due", t.fresh && "fresh"]
      .filter(Boolean).join(" ");
    const meta = [
      isParent
        ? `<span class="chip">${t.subtask_progress.done}/${t.subtask_progress.total}</span>`
        : `<span class="chip recur"${t.window ? ` title="${esc(t.window.times.join(", "))} ${esc(view.reset_tz)}"` : ""}>${icon(RECUR_ICONS[t.recurrence.kind] || "repeat")}${esc(t.recurrence_label)}</span>`,
      // Reset today after a longer wait (e.g. a shop restocked); the server drops it at the next reset.
      t.fresh && !isParent ? `<span class="chip fresh" title="Reset today; this shows until the next daily reset">${icon("new_releases")}Available again</span>` : "",
      windowChip(t),
      t.resets_at ? `<span class="chip">${icon("autorenew")}Resets <span class="countdown" data-until="${t.resets_at}" data-refresh="1"></span></span>` : "",
      t.timer ? `<span class="chip timer">${icon("timer")}Ends <span class="countdown" data-until="${t.timer.ends_at}" data-done="now: time’s up"></span></span>` : "",
      t.reminder_at ? `<span class="chip${t.reminder_due ? " due" : ""}">${icon(t.reminder_due ? "notifications_active" : "notifications")}${esc(fmtWhen(t.reminder_at))}</span>` : "",
      ...t.categories.map((c) => `<button type="button" class="chip tag" data-filter="${esc(c)}" title="Show only #${esc(c)}">${icon("sell")}${esc(c)}</button>`),
    ].join("");
    return `
      <li class="${cls}" data-task="${esc(t.id)}" style="view-transition-name: t-${esc(t.id)}">
        <div class="task-row">
        ${leadControl(t)}
        <div class="body"><span class="title">${esc(t.title)}</span>${counterControl(t)}<div class="meta">${meta}</div></div>
        ${isParent ? iconBtn("toggle-group", collapsed ? "chevron_right" : "expand_more", collapsed ? "Show steps" : "Hide steps", false, "group-toggle") : ""}
        <div class="quick">
          ${iconBtn("up", "arrow_upward", "Move up", t.position === 0)}
          ${iconBtn("down", "arrow_downward", "Move down", t.position === t.siblings - 1)}
        </div>
        <details class="menu">
          <summary aria-label="More actions">${icon("more_vert")}</summary>
          <div class="menu-items">
            ${menuItem("top", "vertical_align_top", "Move to top")}
            ${menuItem("bottom", "vertical_align_bottom", "Move to bottom")}
            ${menuItem("edit", "edit", "Edit")}
            ${t.parent_id ? "" : menuItem("subtask-new", "subdirectory_arrow_right", "Add subtask")}
            ${menuItem("duplicate", "content_copy", "Duplicate")}
            ${t.timer ? menuItem("timer-stop", "timer_off", "Stop timer") : ""}
            ${t.reminder_at ? menuItem("reminder-clear", "notifications_off", "Clear reminder") : menuItem("reminder", "notifications", "Set reminder")}
            ${menuItem("toggle-enabled", t.enabled ? "visibility_off" : "visibility", t.enabled ? "Opt out" : "Opt in")}
            ${menuItem("delete", "delete", "Delete", "danger")}
          </div>
        </details>
        </div>
        ${isParent ? `<ul class="tasks subtasks"${collapsed ? " hidden" : ""}>${t.subtasks.map(renderTask).join("")}</ul>` : ""}
      </li>`;
  }

  // Time-window events: "Closed · opens in …" between windows, "Closes in …" while open and unclaimed.
  // Other inactive items just say why ("Not today").
  function windowChip(t) {
    const until = (iso) => `<span class="countdown" data-until="${iso}" data-refresh="1"></span>`;
    const w = t.window;
    if (w && !w.open) return `<span class="chip">${icon("lock_clock")}Closed · opens ${until(w.opens_at)}</span>`;
    if (w && !t.done) return `<span class="chip">${icon("hourglass_top")}Closes ${until(w.closes_at)}</span>`;
    return t.active ? "" : `<span class="chip">${esc(t.inactive_label)}</span>`;
  }

  // Counters: numbered dots for small targets (click n to set n, click the last lit one to undo it),
  // a 0..target slider for big ones.
  function counterControl(t) {
    const locked = t.active ? "" : ` disabled title="${esc(t.inactive_label)}"`;
    if (t.counter === "dots") {
      const dots = Array.from({ length: t.target }, (_, i) => {
        const n = i + 1;
        const on = n <= t.progress;
        return `<button type="button" class="dot${on ? " on" : ""}" data-action="progress" data-value="${n === t.progress ? n - 1 : n}" aria-pressed="${on}" aria-label="${n} of ${t.target}"${locked}>${n}</button>`;
      });
      return `<div class="dots" role="group" aria-label="${esc(t.title)} progress">${dots.join("")}</div>`;
    }
    if (t.counter === "slider") {
      return `<label class="slider"><input type="range" min="0" max="${t.target}" value="${t.progress}" data-progress-slider aria-label="${esc(t.title)} progress"${locked}><output>${t.progress}/${t.target}</output></label>`;
    }
    return "";
  }

  // "Not today" tasks can't be checked off (a done one can still be unticked).
  function leadControl(t) {
    const locked = !t.active && !t.done ? ` disabled title="${esc(t.inactive_label)}"` : "";
    return `<button type="button" class="check" data-action="toggle" aria-pressed="${t.done}" aria-label="${t.done ? "Mark not done" : "Mark done"}"${locked}>
        ${icon(t.done ? "check_box" : "check_box_outline_blank")}
      </button>`;
  }

  // --- cooldowns tab -----------------------------------------------------------------

  // Standalone timers: soonest first; finished ones ring (and count in the tab and page title)
  // until dismissed.
  function renderTimers() {
    const list = view.countdowns;
    const over = view.countdowns_over;
    $("#timers-progress").text(over ? `${over} up` : list.length ? `${list.length} running` : "");
    $("#timers").html(list.map(renderTimer).join(""));
    document.title = over ? `(${over}) Todo` : "Todo";
  }

  function renderTimer(c) {
    const now = Date.now() + clockOffset;
    const lead = c.over
      ? `<button type="button" class="claim primary" data-action="timer-dismiss" title="Dismiss">${icon("done")}Done</button>`
      : `<span class="timer-icon">${icon("timer")}</span>`;
    const meta = [
      c.over
        ? `<span class="chip ringing">${icon("alarm")}Time’s up · ${esc(fmtClock(Date.parse(c.ends_at), now))}</span>`
        : `<span class="chip">${icon("hourglass_top")}Ends <span class="countdown" data-until="${c.ends_at}" data-refresh="1"></span></span>`,
      `<span class="chip recur" title="The length it was set to">${icon("timer")}${esc(fmtTimeLeft(c.seconds))}</span>`,
    ].join("");
    const bar = c.over ? "" : `
      <div class="refill" data-until="${c.ends_at}" data-span="${c.seconds * 1000}" role="progressbar"
        aria-label="Time passed" aria-valuemin="0" aria-valuemax="100">
        <span style="inline-size: ${(refillAt(c.ends_at, c.seconds * 1000) * 100).toFixed(2)}%"></span></div>`;
    return `
      <li class="task countdown-row${c.over ? " over" : ""}" data-countdown="${esc(c.id)}" style="view-transition-name: d-${esc(c.id)}">
        <div class="task-row">
        ${lead}
        <div class="body"><span class="title">${esc(c.title)}</span><div class="meta">${meta}</div>${bar}</div>
        <div class="quick">
          ${iconBtn("timer-restart", "replay", `Restart: ${fmtTimeLeft(c.seconds)} from now`)}
        </div>
        <details class="menu">
          <summary aria-label="More actions">${icon("more_vert")}</summary>
          <div class="menu-items">
            ${menuItem("timer-edit", "edit", "Edit")}
            ${menuItem("timer-restart", "replay", "Restart")}
            ${menuItem("timer-dismiss", "delete", c.over ? "Dismiss" : "Delete", "danger")}
          </div>
        </details>
        </div>
      </li>`;
  }

  function renderCooldowns() {
    if (view.countdowns) renderTimers();
    const list = view.cooldowns;
    // One badge on the tab: timers that are up plus cooldowns that are full (yellow if a timer is up).
    const over = view.countdowns ? view.countdowns_over : 0;
    const ready = over + view.cooldowns_full;
    $("#timers-ready").text(ready).prop("hidden", !ready).toggleClass("ringing", over > 0)
      .attr("title", `${over} timer${over === 1 ? "" : "s"} up · ${view.cooldowns_full} cooldown${view.cooldowns_full === 1 ? "" : "s"} full`);
    $("#cooldowns-progress").text(list.length ? `${view.cooldowns_full}/${list.length} full` : "");
    $("#cooldowns").html(list.map(renderCooldown).join(""));
  }

  // Still refilling: a bar that fills up towards the next one (kept moving by tick()).
  const refillAt = (untilIso, spanMs) => refillFraction(Date.parse(untilIso), spanMs, Date.now() + clockOffset);
  function refillBar(c) {
    if (c.full) return "";
    const pct = (refillAt(c.next_at, c.minutes * 60000) * 100).toFixed(2);
    return `
      <div class="refill" data-until="${c.next_at}" data-span="${c.minutes * 60000}" role="progressbar"
        aria-label="Refilling the next one" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(pct)}">
        <span style="inline-size: ${pct}%"></span></div>`;
  }

  // Anything can be claimed at any time; claiming empties it and refilling starts over.
  function renderCooldown(c) {
    const plain = c.capacity === 1;
    const count = plain ? "" : ` ${c.level}`;
    const lead = c.full
      ? `<button type="button" class="claim primary" data-action="claim">${icon("redeem")}Claim${count}</button>`
      : c.level > 0
        ? `<button type="button" class="claim" data-action="claim">${icon("redeem")}Claim${count}</button>`
        : `<button type="button" class="claim" data-action="claim" title="Restarts the wait">${icon("bolt")}Claim early</button>`;
    const until = (iso, refresh) => `<span class="countdown" data-until="${iso}"${refresh ? ' data-refresh="1"' : ""}></span>`;
    const meta = [
      plain ? "" : `<span class="chip level${c.full ? " full" : ""}">${icon("stacks")}${c.level}/${c.capacity}</span>`,
      `<span class="chip recur">${icon("hourglass_empty")}${esc(c.label)}</span>`,
      c.full ? "" : plain
        ? `<span class="chip">${icon("hourglass_top")}Ready ${until(c.next_at, true)}</span>`
        : `<span class="chip">${icon("hourglass_top")}+1 ${until(c.next_at, true)}</span><span class="chip">Full ${until(c.full_at)}</span>`,
    ].join("");
    return `
      <li class="task cooldown${c.level === 0 ? " waiting" : ""}" data-cooldown="${esc(c.id)}" style="view-transition-name: c-${esc(c.id)}">
        <div class="task-row">
        ${lead}
        <div class="body"><span class="title">${esc(c.title)}</span><div class="meta">${meta}</div>${refillBar(c)}</div>
        <div class="quick">
          ${iconBtn("up", "arrow_upward", "Move up", c.position === 0)}
          ${iconBtn("down", "arrow_downward", "Move down", c.position === c.siblings - 1)}
        </div>
        <details class="menu">
          <summary aria-label="More actions">${icon("more_vert")}</summary>
          <div class="menu-items">
            ${menuItem("top", "vertical_align_top", "Move to top")}
            ${menuItem("bottom", "vertical_align_bottom", "Move to bottom")}
            ${c.full ? "" : menuItem("unclaim", "undo", c.capacity === 1 ? "Unclaim" : "Mark full")}
            ${menuItem("edit", "edit", "Edit")}
            ${menuItem("delete", "delete", "Delete", "danger")}
          </div>
        </details>
        </div>
      </li>`;
  }

  function openCooldownDialog(c) {
    editingCooldown = c;
    const f = $cooldownForm[0];
    $("#cooldown-dialog-title").text(c ? "Edit cooldown" : "Add cooldown");
    f.reset();
    f.elements.title.value = c ? c.title : "";
    f.elements.hours.value = c ? Math.floor(c.minutes / 60) : 8;
    f.elements.minutes.value = c ? c.minutes % 60 : 0;
    f.elements.capacity.value = c ? c.capacity : 1;
    f.elements.current.value = c ? c.level : 0;
    f.elements.progress_kind.value = "";
    syncValueMax();
    // Editing: show where the next one is now, as a reference for the optional progress field.
    const now = Date.now() + clockOffset;
    $("#progress-now").text(c && c.next_at ? `Right now: next one in ${fmtDuration(Date.parse(c.next_at) - now)}.` : "");
    $cooldownForm.find(".form-error").prop("hidden", true);
    cooldownDialog.showModal();
    f.elements.title.focus();
  }

  $cooldownForm.on("submit", function (e) {
    e.preventDefault();
    const f = this;
    let fields;
    try {
      fields = cooldownFields({
        title: f.elements.title.value,
        hours: f.elements.hours.value,
        minutes: f.elements.minutes.value,
        capacity: f.elements.capacity.value,
        current: f.elements.current.value,
        progressKind: f.elements.progress_kind.value,
        progressHours: f.elements.p_hours.value,
        progressMinutes: f.elements.p_minutes.value,
      });
    } catch (err) {
      return $cooldownForm.find(".form-error").text(err.message).prop("hidden", false);
    }
    const payload = editingCooldown
      ? { type: "edit_cooldown", cooldown_id: editingCooldown.id, ...fields }
      : { type: "add_cooldown", ...fields };
    send(payload).done(() => cooldownDialog.close());
  });

  $("#cooldown-cancel").on("click", () => cooldownDialog.close());

  // Lets the browser flag "Current value" > "Holds up to" before sending; the core checks it too.
  function syncValueMax() {
    const f = $cooldownForm[0];
    f.elements.current.max = Math.max(1, Math.floor(+f.elements.capacity.value || 1));
  }
  $cooldownForm.on("input change", '[name="capacity"]', syncValueMax);

  // --- timers ------------------------------------------------------------------------

  // Quick add: a name and the time left, Enter to start.
  $("#timer-add").on("submit", function (e) {
    e.preventDefault();
    const f = this;
    const $err = $(f).find(".form-error");
    let fields;
    try {
      fields = timerFields(f.elements.title.value, f.elements.hours.value, f.elements.minutes.value);
    } catch (err) {
      return $err.text(err.message).prop("hidden", false);
    }
    $err.prop("hidden", true);
    send({ type: "add_countdown", ...fields }).done(() => {
      f.reset();
      f.elements.title.focus();
    });
  });
  $("#timer-add").on("input", () => $("#timer-add .form-error").prop("hidden", true));

  // Hours and minutes fields: a dropdown of common values (1–12 h, 15/30/45 min) while the field has
  // focus. Pressing an option doesn't take focus from the field, so the list doesn't vanish mid-tap
  // (iOS doesn't focus buttons).
  const timeMenu = (input) => $(input).closest(".time-picker").find(".time-menu");
  $(document)
    .on("focus click", ".time-picker input", function () { timeMenu(this).prop("hidden", false); })
    .on("blur", ".time-picker input", function () { timeMenu(this).prop("hidden", true); })
    .on("keydown", ".time-picker input", function (e) {
      if (e.key === "Escape" && !timeMenu(this).prop("hidden")) {
        e.preventDefault(); // close the list, not the dialog
        timeMenu(this).prop("hidden", true);
      }
    })
    .on("mousedown pointerdown", ".time-menu button", (e) => e.preventDefault())
    .on("click", ".time-menu button", function () {
      const input = $(this).closest(".time-picker").find("input")[0];
      input.value = this.dataset.value;
      $(input).trigger("input");
      $(this).closest(".time-menu").prop("hidden", true);
      input.blur(); // done with it: on a phone this also drops the keyboard
    });

  let editingTimer = null;
  let timeShown = "";  // the hours/minutes the dialog pre-filled: left untouched, the timer runs on as it is
  const timeFieldsOf = (f) => `${f.elements.hours.value}:${f.elements.minutes.value}`;
  function openTimerDialog(c) {
    editingTimer = c;
    const f = $("#timer-form")[0];
    f.elements.title.value = c.title;
    // A finished one starts blank, with its last length as a hint. So does one with more than 23h
    // left (only possible through the API), which the fields can't hold; blank keeps it as it is.
    const left = splitTimeLeft(c.over ? 0 : (Date.parse(c.ends_at) - (Date.now() + clockOffset)) / 1000);
    const blank = c.over || left.hours > 23;
    const hint = splitTimeLeft(c.seconds);
    f.elements.hours.value = blank ? "" : left.hours;
    f.elements.minutes.value = blank ? "" : left.minutes;
    f.elements.hours.placeholder = blank ? Math.min(hint.hours, 23) : "";
    f.elements.minutes.placeholder = blank ? hint.minutes : "";
    timeShown = timeFieldsOf(f);
    $("#timer-form .form-error").prop("hidden", true);
    timerDialog.showModal();
    f.elements.hours.focus();
  }

  $("#timer-form").on("submit", function (e) {
    e.preventDefault();
    const f = this;
    const changed = timeFieldsOf(f) !== timeShown && timeFieldsOf(f) !== ":";
    const payload = { type: "edit_countdown", countdown_id: editingTimer.id, title: f.elements.title.value.trim() };
    try {
      // Untouched (or both cleared), the time left stays as it is.
      if (changed) Object.assign(payload, timerFields(payload.title, f.elements.hours.value, f.elements.minutes.value));
      else if (!payload.title) throw new Error("Name the timer, e.g. Mine Venture.");
    } catch (err) {
      return $("#timer-form .form-error").text(err.message).prop("hidden", false);
    }
    send(payload).done(() => timerDialog.close());
  });
  $("#timer-cancel").on("click", () => timerDialog.close());

  $("#panel-cooldowns").on("click", "[data-countdown] [data-action]", function () {
    const rowEl = this.closest("[data-countdown]");
    const c = view.countdowns.find((x) => x.id === rowEl.dataset.countdown);
    if (!c) return;
    $(this).closest(".menu").removeAttr("open");
    switch (this.dataset.action) {
      case "timer-dismiss":
        markPending(this, rowEl);
        return send({ type: "remove_countdown", countdown_id: c.id });
      case "timer-restart": return send({ type: "restart_countdown", countdown_id: c.id });
      case "timer-edit": return openTimerDialog(c);
    }
  });

  $("#panel-cooldowns").on("click", function (e) {
    const actionEl = e.target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    if (action === "cooldown-new") return openCooldownDialog(null);
    const rowEl = e.target.closest("[data-cooldown]");
    const c = rowEl && view.cooldowns.find((x) => x.id === rowEl.dataset.cooldown);
    if (!c) return;
    const move = (index) => send({ type: "move_cooldown", cooldown_id: c.id, index });
    switch (action) {
      case "claim":
        markPending(actionEl, rowEl);
        return send({ type: "claim", cooldown_id: c.id });
      case "unclaim": return send({ type: "unclaim", cooldown_id: c.id });
      case "up": return move(c.position - 1);
      case "down": return move(c.position + 1);
      case "top": return move(0);
      case "bottom": return move(c.siblings - 1);
      case "edit": return openCooldownDialog(c);
      case "delete": return confirm(`Delete “${c.title}”?`) && send({ type: "remove_cooldown", cooldown_id: c.id });
    }
  });

  // --- browse the preset: pick items you don't have and import them together -------------------

  function openBrowse() {
    $("#browse-list").html('<p class="muted">Loading…</p>');
    $("#browse-form .form-error").prop("hidden", true);
    updateBrowseButtons();
    browseDialog.showModal();
    $.ajax({ url: API + "missing", dataType: "json" })
      .done((r) => renderBrowse(r.items))
      .fail(() => $("#browse-list").html('<p class="form-error">Couldn’t load the preset.</p>'));
  }

  function renderBrowse(items) {
    if (!items.length) return $("#browse-list").html('<p class="empty">You already have everything in the preset.</p>');
    const sections = new Map(); // where it goes -> rows, in preset order
    for (const it of items) {
      if (!sections.has(it.bay_name)) sections.set(it.bay_name, []);
      sections.get(it.bay_name).push(it);
    }
    const row = (it) => {
      const notes = [
        it.kind === "step" ? `adds a step to “${esc(it.group)}”` : "",
        esc(it.label),
        it.steps ? `${it.steps} step${it.steps === 1 ? "" : "s"}` : "",
        it.hidden ? "hidden from Suggested" : "",
      ].filter(Boolean).join(" · ");
      return `
        <li><label class="check-label">
          <input type="checkbox" name="pick" value="${esc(it.id)}">
          <span><span class="b-title">${esc(it.title)}</span> <span class="muted">${notes}</span></span>
        </label></li>`;
    };
    $("#browse-list").html([...sections].map(([where, rows]) => `
      <section class="browse-section">
        <h3>${icon(where === "Cooldowns" ? "hourglass_empty" : "view_agenda")}${esc(where)}</h3>
        <ul>${rows.map(row).join("")}</ul>
      </section>`).join(""));
    updateBrowseButtons();
  }

  const picked = () => $("#browse-list [name=pick]:checked").map(function () { return this.value; }).get();

  function updateBrowseButtons() {
    const n = picked().length;
    const total = $("#browse-list [name=pick]").length;
    $("#browse-import").prop("disabled", !n).text(n ? `Import ${n}` : "Import");
    $("#browse-all").prop("disabled", !total).text(total && n === total ? "Select none" : "Select all");
  }

  $("#browse").on("click", openBrowse);
  $("#browse-cancel").on("click", () => browseDialog.close());
  $("#browse-list").on("change", "[name=pick]", updateBrowseButtons);
  $("#browse-all").on("click", function () {
    const $boxes = $("#browse-list [name=pick]");
    $boxes.prop("checked", picked().length !== $boxes.length);
    updateBrowseButtons();
  });
  $("#browse-form").on("submit", function (e) {
    e.preventDefault();
    const ids = picked();
    if (!ids.length) return;
    markPending(document.getElementById("browse-import"));
    api("POST", "import", { ids })
      .done(() => { browseDialog.close(); toast(`Imported ${ids.length}.`); })
      .always(updateBrowseButtons);
  });

  // --- tabs (the open one is kept in the URL hash, e.g. …/todo/#timers) ----------------

  function showTab(name) {
    $("[role=tab]").each(function () {
      const on = this.dataset.tab === name;
      this.setAttribute("aria-selected", on);
      document.getElementById(this.getAttribute("aria-controls")).hidden = !on;
    });
    history.replaceState(null, "", name === "tasks" ? location.pathname + location.search : "#" + name);
  }

  $(".tabs").on("click", "[role=tab]", function () { showTab(this.dataset.tab); });
  showTab(["#timers", "#cooldowns"].includes(location.hash) ? "timers" : "tasks"); // #cooldowns: old links

  // --- countdowns --------------------------------------------------------------------

  const localZone = Intl.DateTimeFormat().resolvedOptions().timeZone;

  // Countdowns read "in 2h 05m" or, after a tap on any of them, "at 20:35" (your local time).
  // Either way, hovering shows the full local date and time. Remembered per browser.
  const CLOCK_KEY = "todo.clockTimes";
  let showClock = false;
  try { showClock = localStorage.getItem(CLOCK_KEY) === "1"; } catch { /* storage blocked */ }

  // "20:35" today, "Sat 20:35" within the week, "3 Oct 20:35" beyond that. Local time zone.
  function fmtClock(ms, now) {
    const d = new Date(ms);
    const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    if (d.toDateString() === new Date(now).toDateString()) return time;
    if (ms - now < 6 * 86400000) return `${d.toLocaleDateString([], { weekday: "short" })} ${time}`;
    return `${d.toLocaleDateString([], { day: "numeric", month: "short" })} ${time}`;
  }

  const fmtFull = (ms) =>
    new Date(ms).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) +
    ` (${localZone})`;

  $(document).on("click", ".chip:has(.countdown:not([data-fixed])), .clocks .countdown", function (e) {
    e.stopPropagation();
    showClock = !showClock;
    try { localStorage.setItem(CLOCK_KEY, showClock ? "1" : "0"); } catch { /* storage blocked */ }
    tick();
  });

  function tick() {
    $("#local-time").text(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }));
    $("#local-zone").text(localZone);
    const now = Date.now() + clockOffset;
    let needsRefresh = false;
    $(".refill").each(function () {
      const frac = refillAt(this.dataset.until, +this.dataset.span);
      this.firstElementChild.style.inlineSize = `${(frac * 100).toFixed(2)}%`;
      this.setAttribute("aria-valuenow", Math.round(frac * 100));
    });
    $(".countdown").each(function () {
      const until = Date.parse(this.dataset.until);
      const left = until - now;
      const fixed = this.dataset.fixed; // e.g. the dialog preview, which shows the date next to it
      if (!fixed) {
        const tip = `${fmtFull(until)} · tap to show ${showClock ? "time left" : "clock times"}`;
        const host = this.closest(".chip") || this;
        if (host.title !== tip) host.title = tip;
      }
      if (left > 0) {
        this.textContent = fixed ? fmtDuration(left) : showClock ? `at ${fmtClock(until, now)}` : `in ${fmtDuration(left)}`;
      } else {
        this.textContent = this.dataset.done || "now";
        $(this).parent().addClass("ringing");
        if (this.dataset.refresh && !this.dataset.fired) {
          this.dataset.fired = "1";
          needsRefresh = true;
        }
      }
    });
    if (needsRefresh) refresh();
  }

  setInterval(tick, 1000);
  setInterval(refresh, 60_000); // catch daily resets and newly-due reminders
  document.addEventListener("visibilitychange", () => document.visibilityState === "visible" && refresh());

  // --- task dialog -------------------------------------------------------------------

  function openTaskDialog(opts) {
    editing = opts;
    const t = opts.task;
    const r = t ? t.recurrence : { kind: "daily" };
    const f = $form[0];
    $("#task-dialog-title").text(t ? "Edit task" : opts.parentId ? `Add subtask to “${findTask(opts.parentId).title}”` : "Add task");
    f.reset();
    f.elements.title.value = t ? t.title : "";
    f.elements.categories.value = t ? t.categories.join(", ") : (filters.filter || "");
    f.elements.kind.value = r.kind;
    f.elements.weekday.value = String(r.weekday ?? 0);
    $form.find('[name="days"]').each(function () { this.checked = (r.days || []).includes(+this.value); });
    f.elements.target.value = t && t.target >= 2 ? t.target : "";
    // Counter and tags live under the collapsed "Counter & tags": open it whenever either is already set.
    document.getElementById("task-more").open = Boolean(f.elements.target.value || f.elements.categories.value);
    updateMoreSummary();
    f.elements.every.value = r.every || 3;
    f.elements.anchor.value = r.anchor || new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD
    $(".reset-tz").text(view.reset_tz);
    $("#window-rows").empty();
    (r.windows || [[0, 60]]).forEach(([start, end]) => addWindowRow(start, end));
    $form.find(".form-error").prop("hidden", true);
    updatePreview();
    dialog.showModal();
    f.elements.title.focus();
  }

  // The repeat the dialog describes (logic.js checks it); throws an Error with a message.
  function readRecurrence(f) {
    return buildRecurrence({
      kind: f.elements.kind.value,
      weekday: f.elements.weekday.value,
      days: $form.find('[name="days"]:checked').map(function () { return +this.value; }).get(),
      windows: windowTexts(),
      every: f.elements.every.value,
      anchor: f.elements.anchor.value,
    });
  }

  $form.on("submit", function (e) {
    e.preventDefault();
    const f = this;
    let recurrence;
    try {
      recurrence = readRecurrence(f);
    } catch (err) {
      return $form.find(".form-error").text(err.message).prop("hidden", false);
    }
    const fields = {
      title: f.elements.title.value,
      categories: splitTags(f.elements.categories.value),
      target: counterTarget(f.elements.target.value), // blank = checkbox
      recurrence,
    };
    const payload = editing.task
      ? { type: "edit_task", task_id: editing.task.id, ...fields }
      : { type: "add_task", bay_id: editing.bayId, parent_id: editing.parentId || null, ...fields };
    send(payload).done(() => dialog.close());
  });

  $("#task-cancel").on("click", () => dialog.close());

  // What's set under the collapsed "Counter & tags", e.g. "· ×5 · #ads".
  function updateMoreSummary() {
    const f = $form[0];
    $("#task-more-summary").text(moreSummary(f.elements.target.value, f.elements.categories.value));
  }
  $form.on("input", '[name="target"], [name="categories"]', updateMoreSummary);

  // Live "Resets in …" preview while choosing a repeat. The server's core does the date maths.
  const REPEAT_FIELDS = '[name="kind"], [name="weekday"], [name="days"], [name="every"], [name="anchor"], [data-w]';

  // --- daily windows editor (times are minutes after the daily reset, shown as HH:MM) ----------

  // Each row's [opens, closes] as typed, e.g. ["00:00", "12:00"].
  const windowTexts = () => $("#window-rows li").map(function () {
    return [[$(this).find('[data-w="start"]').val(), $(this).find('[data-w="end"]').val()]];
  }).get();

  function addWindowRow(start, end) {
    $("#window-rows").append(`
      <li>
        <input type="time" data-w="start" value="${minutesToHhmm(start)}" aria-label="Opens">
        <span class="muted">–</span>
        <input type="time" data-w="end" value="${minutesToHhmm(end)}" aria-label="Closes">
        <button type="button" class="icon-btn" data-window-remove title="Remove window" aria-label="Remove window">${icon("close")}</button>
      </li>`);
  }

  // A new row repeats the pattern so far (logic.js nextWindow). Every row stays editable.
  $("#window-add").on("click", function () {
    const next = nextWindow(windowTexts().map(([s, e]) => windowRange(s, e)));
    if (!next) return toast("That would pass midnight; the day's windows repeat every day anyway.");
    addWindowRow(...next);
    schedulePreview();
  });

  $("#window-rows").on("click", "[data-window-remove]", function () {
    $(this).closest("li").remove();
    schedulePreview();
  });
  let previewTimer;
  let previewSeq = 0;

  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 150);
  }

  function updatePreview() {
    let recurrence;
    try {
      recurrence = readRecurrence($form[0]);
    } catch {
      return showPreview(null);
    }
    const seq = ++previewSeq;
    $.ajax({
      url: API + "preview",
      method: "POST",
      contentType: "application/json",
      data: JSON.stringify({ recurrence }),
      headers: { "X-CSRF-Token": CSRF },
      dataType: "json",
    }).done((r) => seq === previewSeq && showPreview(r.next_reset_at, recurrence.kind))
      .fail(() => seq === previewSeq && showPreview(null));
  }

  function showPreview(iso, kind) {
    const $p = $("#reset-preview");
    if (!iso) return $p.prop("hidden", true);
    $p.find(".label").text(kind === "windows" ? "If done now, the next window opens in" : "Resets in");
    $p.find(".countdown").attr("data-until", iso);
    $p.find(".when").text("· " + new Date(iso).toLocaleString([], { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }));
    $p.prop("hidden", false);
    tick();
  }

  $form.on("input change", REPEAT_FIELDS, schedulePreview);

  // −/+ next to "Resets in": move the "Resets on" day, which moves the reset a day earlier/later.
  $form.on("click", "[data-nudge]", function () {
    const anchor = $form[0].elements.anchor;
    const d = new Date(anchor.value + "T00:00:00Z"); // date-only maths in UTC avoids DST/zone shifts
    d.setUTCDate(d.getUTCDate() + +this.dataset.nudge);
    anchor.value = d.toISOString().slice(0, 10);
    updatePreview();
  });

  $("#bays")
    .on("input", "[data-progress-slider]", function () {
      $(this).next("output").text(`${this.value}/${this.max}`);
    })
    .on("change", "[data-progress-slider]", function () {
      const taskEl = this.closest(".task");
      markPending(null, taskEl);
      send({ type: "set_progress", task_id: taskEl.dataset.task, value: +this.value });
    });

  // --- events ------------------------------------------------------------------------

  $("#add-bay").on("submit", function (e) {
    e.preventDefault();
    const input = this.elements.name;
    send({ type: "add_bay", name: input.value }).done(() => (input.value = ""));
  });

  $("#suggested").on("click", "[data-suggested-action]", function () {
    const li = this.closest("[data-suggested]");
    markPending(this, li);
    api("POST", "suggested", { id: li.dataset.suggested, action: this.dataset.suggestedAction });
  });

  $("#undo").on("click", undo);
  $("#redo").on("click", redo);

  $("#filters").on("click", "#hide-done", function () {
    hideDone = !hideDone;
    try { localStorage.setItem(HIDE_DONE_KEY, hideDone ? "1" : "0"); } catch { /* storage blocked */ }
    renderFilters();
    refresh();
  });

  $("#filters").on("click", "[data-filters-clear]", () => setFilters(NO_FILTERS));
  $("#filters").on("click", "[data-tag-hide]", function () {
    setFilters(toggleHideTag(filters, this.dataset.tagHide));
  });

  // A tag's name, in the filter bar or on a task row, toggles "only this tag".
  $(document).on("click", "[data-filter]", function (e) {
    e.stopPropagation();
    setFilters(toggleOnlyTag(filters, this.dataset.filter));
  });

  // Only one kebab menu open at a time; close menus on outside click.
  // `toggle` does not bubble, so listen in the capture phase.
  for (const id of ["bays", "panel-cooldowns"]) {
    document.getElementById(id).addEventListener("toggle", (e) => {
      if (e.target.matches?.(".menu") && e.target.open) $(".menu[open]").not(e.target).removeAttr("open");
    }, true);
  }
  $(document).on("click", (e) => {
    if (!$(e.target).closest(".menu").length) $(".menu[open]").removeAttr("open");
  });

  $("#bays").on("click", function (e) {
    const taskEl = e.target.closest(".task");
    const taskId = taskEl && taskEl.dataset.task;

    const actionEl = e.target.closest("[data-action]");
    if (!actionEl) return;
    const action = actionEl.dataset.action;
    const bayId = e.target.closest(".bay").dataset.bay;
    const bay = view.bays.find((b) => b.id === bayId);
    const bayIndex = bay.position; // stored order, correct even when a #tag filter hides bays
    const t = taskId ? findTask(taskId) : null;

    switch (action) {
      case "bay-up": return send({ type: "move_bay", bay_id: bayId, index: bayIndex - 1 });
      case "bay-down": return send({ type: "move_bay", bay_id: bayId, index: bayIndex + 1 });
      case "bay-rename": {
        const name = prompt("Rename bay", bay.name);
        return name && send({ type: "rename_bay", bay_id: bayId, name });
      }
      case "bay-remove":
        return confirm(`Remove “${bay.name}” and all its tasks?`) && send({ type: "remove_bay", bay_id: bayId });
      case "task-new": return openTaskDialog({ bayId });

      case "toggle":
        markPending(actionEl, taskEl, !t.done);
        return send({ type: t.done ? "uncomplete" : "complete", task_id: t.id });
      case "progress": {
        const value = +actionEl.dataset.value;
        $(actionEl).parent().children().each((i, dot) => dot.classList.toggle("on", i < value));
        markPending(null, taskEl);
        return send({ type: "set_progress", task_id: t.id, value });
      }
      case "up": return send({ type: "move_task", task_id: t.id, bay_id: bayId, index: t.position - 1 });
      case "down": return send({ type: "move_task", task_id: t.id, bay_id: bayId, index: t.position + 1 });
      case "top": return send({ type: "move_to_top", task_id: t.id });
      case "bottom": return send({ type: "move_to_bottom", task_id: t.id });
      case "edit": return openTaskDialog({ task: t });
      case "toggle-group": {
        const hidden = taskEl.querySelector(":scope > .subtasks").hidden;
        groupChoice.set(t.id, { open: hidden, done: t.done });
        return render();
      }
      case "subtask-new": return openTaskDialog({ bayId, parentId: t.id });
      case "duplicate": return send({ type: "duplicate_task", task_id: t.id });
      case "timer-stop": return send({ type: "stop_timer", task_id: t.id });
      case "reminder": {
        const input = prompt("Remind at (e.g. 18:30) or in (e.g. 30m, 2h)", "1h");
        if (input === null) return;
        const at = parseWhen(input, new Date());
        return at ? send({ type: "set_reminder", task_id: t.id, at: at.toISOString() }) : toast("Couldn’t read that time.");
      }
      case "reminder-clear": return send({ type: "clear_reminder", task_id: t.id });
      case "toggle-enabled": return send({ type: "set_enabled", task_id: t.id, enabled: !t.enabled });
      case "delete": return confirm(`Delete “${t.title}”?`) && send({ type: "remove_task", task_id: t.id });
    }
  });

  $(document).on("keydown", (e) => {
    if ($("dialog[open]").length || $(e.target).is("input, select, textarea")) return;
    if (!(e.ctrlKey || e.metaKey)) return;
    const key = e.key.toLowerCase();
    if (key === "z" && !e.shiftKey) { e.preventDefault(); undo(); }
    else if ((key === "z" && e.shiftKey) || key === "y") { e.preventDefault(); redo(); }
  });

  setView(JSON.parse(document.getElementById("initial-view").textContent));
  if (hideDone || filters.hidden.length) refresh(); // the page is rendered unfiltered; narrow it to what's left
});
