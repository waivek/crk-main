// Thin client: renders the server's view model and sends commands. No business logic here.
$(function () {
  "use strict";

  const BASE = $('meta[name="todo-base"]').attr("content");
  const API = $('meta[name="todo-api"]').attr("content");
  const CSRF = $('meta[name="csrf-token"]').attr("content");
  const dialog = document.getElementById("task-dialog");
  const $form = $("#task-form");
  const cooldownDialog = document.getElementById("cooldown-dialog");
  const browseDialog = document.getElementById("browse-dialog");
  const $cooldownForm = $("#cooldown-form");

  let view = null;
  let clockOffset = 0;   // server clock minus client clock, in ms
  let filter = null;     // selected category
  // "Hide done": remembered per browser (a convenience; the page works without storage).
  const HIDE_DONE_KEY = "todo.hideDone";
  let hideDone = false;
  try { hideDone = localStorage.getItem(HIDE_DONE_KEY) === "1"; } catch { /* storage blocked */ }
  // Groups the user opened/closed by hand: id -> {open, done}. A choice only sticks while the
  // group's done state is unchanged, so finishing a group collapses it again.
  const groupChoice = new Map();
  let editing = null;    // {bayId, parentId} for add, {task} for edit
  let editingCooldown = null;  // the cooldown being edited, or null when adding one

  // --- helpers -----------------------------------------------------------------------

  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const RECUR_ICONS = {
    once: "looks_one", daily: "today", weekly: "date_range",
    days_of_week: "calendar_view_week", interval: "event_repeat", windows: "schedule",
  };
  const icon = (name) => `<span class="material-symbols-outlined" aria-hidden="true">${name}</span>`;
  const iconBtn = (action, name, label, disabled = false, cls = "") =>
    `<button type="button" class="icon-btn ${cls}" data-action="${action}" title="${label}" aria-label="${label}"${disabled ? " disabled" : ""}>${icon(name)}</button>`;
  const menuItem = (action, name, label, cls = "") =>
    `<button type="button" class="${cls}" data-action="${action}">${icon(name)}${label}</button>`;

  function findTask(id) {
    const search = (tasks) => {
      for (const t of tasks) {
        if (t.id === id) return t;
        const found = search(t.subtasks);
        if (found) return found;
      }
      return null;
    };
    for (const b of view.bays) {
      const found = search(b.tasks.concat(b.opted_out));
      if (found) return found;
    }
    return null;
  }

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

  const fmtWhen = (iso) =>
    new Date(iso).toLocaleString([], { weekday: "short", hour: "2-digit", minute: "2-digit" });

  // "10" / "10m" -> minutes, "2h", "90s", "1:30" (h:mm). Returns seconds or null.
  function parseDuration(text) {
    const s = (text || "").trim().toLowerCase();
    let m;
    if ((m = s.match(/^(\d+)\s*(m|min)?$/))) return +m[1] * 60;
    if ((m = s.match(/^(\d+)\s*h$/))) return +m[1] * 3600;
    if ((m = s.match(/^(\d+)\s*s$/))) return +m[1];
    if ((m = s.match(/^(\d+):(\d{2})$/))) return +m[1] * 3600 + +m[2] * 60;
    return null;
  }

  // "+30" / "30m" / "2h" from now, or "18:30" local time (today, else tomorrow). Returns Date or null.
  function parseWhen(text) {
    const s = (text || "").trim();
    let m;
    if ((m = s.match(/^(\d{1,2}):(\d{2})$/))) {
      const d = new Date();
      d.setHours(+m[1], +m[2], 0, 0);
      if (d <= new Date()) d.setDate(d.getDate() + 1);
      return d;
    }
    const secs = parseDuration(s.replace(/^\+/, ""));
    return secs ? new Date(Date.now() + secs * 1000) : null;
  }

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

  // The server narrows the view (core/view.py filter_view): by #tag, and/or to unfinished tasks.
  function viewQuery() {
    const q = new URLSearchParams();
    if (filter) q.set("category", filter);
    if (hideDone) q.set("hide_done", "1");
    const s = q.toString();
    return s ? "?" + s : "";
  }

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

  function renderFilters() {
    const chip = (cat, labelHtml, cls = "") =>
      `<button type="button" class="chip ${cls}" data-filter="${esc(cat)}" aria-pressed="${filter === cat || (!filter && cat === "")}">${labelHtml}</button>`;
    const toggle = `<button type="button" class="chip hide-done" id="hide-done" aria-pressed="${hideDone}"
      title="Show only what's left to do">${icon(hideDone ? "check_box" : "check_box_outline_blank")}Hide done</button>`;
    const tags = view.categories.length
      ? `<span class="filter-sep" aria-hidden="true"></span>` + chip("", "All") + view.categories.map((c) => chip(c, icon("sell") + esc(c), "tag")).join("")
      : "";
    $("#filters").html(toggle + tags);
  }

  // The server already narrowed the view to the selected #tag (see core/view.py filter_view).
  // On narrow screens the bay's move/rename/remove buttons fold into a ⋮ menu (see app.css).
  function renderBay(b) {
    const first = b.position === 0;
    const last = b.position === view.bay_count - 1;
    const tasks = b.tasks;
    const off = b.opted_out;
    return `
      <section class="bay" data-bay="${esc(b.id)}" style="view-transition-name: b-${esc(b.id)}">
        <header class="bay-head">
          <span class="bay-num">Bay ${b.position + 1}</span>
          <h2>${esc(b.name)}</h2>
          <span class="progress">${b.progress.done}/${b.progress.total}</span>
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
        ${b.hidden_done ? `<p class="hidden-done">${icon("done_all")}${tasks.length ? "" : "All done · "}${b.hidden_done} done hidden</p>` : ""}
        ${off.length ? `<details class="opted-out"><summary>Opted out (${off.length})</summary><ul class="tasks">${off.map(renderTask).join("")}</ul></details>` : ""}
      </section>`;
  }

  function renderTask(t) {
    const isParent = t.is_group;
    const choice = groupChoice.get(t.id);
    const collapsed = choice && choice.done === t.done ? !choice.open : t.collapsed;
    const cls = ["task", t.done && "done", !t.active && "inactive", !t.enabled && "off", t.reminder_due && "due"]
      .filter(Boolean).join(" ");
    const meta = [
      isParent
        ? `<span class="chip">${t.subtask_progress.done}/${t.subtask_progress.total}</span>`
        : `<span class="chip recur"${t.window ? ` title="${esc(t.window.times.join(", "))} ${esc(view.reset_tz)}"` : ""}>${icon(RECUR_ICONS[t.recurrence.kind] || "repeat")}${esc(t.recurrence_label)}</span>`,
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
            ${t.timer ? menuItem("timer-stop", "timer_off", "Stop timer") : menuItem("timer", "timer", "Start timer")}
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

  function renderCooldowns() {
    const list = view.cooldowns;
    $("#cooldowns-full").text(view.cooldowns_full).prop("hidden", !view.cooldowns_full);
    $("#cooldowns-progress").text(list.length ? `${view.cooldowns_full}/${list.length} full` : "");
    $("#cooldowns").html(list.map(renderCooldown).join(""));
  }

  // Still refilling: a bar that fills up towards the next one (kept moving by tick()).
  const refillFraction = (untilIso, spanMs) =>
    Math.min(1, Math.max(0, 1 - (Date.parse(untilIso) - (Date.now() + clockOffset)) / spanMs));
  function refillBar(c) {
    if (c.full) return "";
    const pct = (refillFraction(c.next_at, c.minutes * 60000) * 100).toFixed(2);
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
    const minutes = (+f.elements.hours.value || 0) * 60 + (+f.elements.minutes.value || 0);
    if (minutes <= 0) return $cooldownForm.find(".form-error").text("The refill time must be longer than 0 minutes.").prop("hidden", false);
    const fields = {
      title: f.elements.title.value,
      minutes,
      capacity: Math.floor(+f.elements.capacity.value || 0),
      value: Math.floor(+f.elements.current.value || 0),
    };
    const kind = f.elements.progress_kind.value;
    if (kind) {
      const progress = (+f.elements.p_hours.value || 0) * 60 + (+f.elements.p_minutes.value || 0);
      if (progress >= minutes) return $cooldownForm.find(".form-error").text("The time left or passed must be less than the refill time.").prop("hidden", false);
      fields.progress = { [kind]: progress };
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

  // --- tabs (the open one is kept in the URL hash, e.g. …/todo/#cooldowns) -------------

  function showTab(name) {
    $("[role=tab]").each(function () {
      const on = this.dataset.tab === name;
      this.setAttribute("aria-selected", on);
      document.getElementById(this.getAttribute("aria-controls")).hidden = !on;
    });
    history.replaceState(null, "", name === "tasks" ? location.pathname + location.search : "#" + name);
  }

  $(".tabs").on("click", "[role=tab]", function () { showTab(this.dataset.tab); });
  showTab(location.hash === "#cooldowns" ? "cooldowns" : "tasks");

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
      const frac = refillFraction(this.dataset.until, +this.dataset.span);
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
    f.elements.categories.value = t ? t.categories.join(", ") : (filter || "");
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

  const checkedDays = () => $form.find('[name="days"]:checked').map(function () { return +this.value; }).get();

  function readRecurrence(f) {
    const kind = f.elements.kind.value;
    switch (kind) {
      case "weekly": {
        const days = checkedDays();
        const weekday = +f.elements.weekday.value;
        return days.length && days.length < 7 ? { kind, weekday, days } : { kind, weekday };
      }
      case "days_of_week": {
        const days = checkedDays();
        if (!days.length) throw new Error("Pick at least one day.");
        return { kind, days };
      }
      case "windows": {
        const windows = $("#window-rows li").map(function () {
          const start = hhmmToMinutes($(this).find('[data-w="start"]').val());
          const end = hhmmToMinutes($(this).find('[data-w="end"]').val());
          if (start === null || end === null) throw new Error("Fill in every window's times (or remove the row).");
          return [[start, end === 0 ? 24 * 60 : end]]; // ending at 00:00 means midnight at the end of the day
        }).get();
        if (!windows.length) throw new Error("Add at least one window.");
        return { kind, windows };
      }
      case "interval": {
        const every = Math.floor(+f.elements.every.value || 0);
        if (every < 2) throw new Error("Every N days needs N of at least 2.");
        if (!f.elements.anchor.value) throw new Error("Pick a day it resets.");
        return { kind, every, anchor: f.elements.anchor.value };
      }
      default:
        return { kind };
    }
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
      categories: f.elements.categories.value.split(",").map((s) => s.trim()).filter(Boolean),
      target: Math.floor(+f.elements.target.value || 0) >= 2 ? Math.floor(+f.elements.target.value) : 0, // blank = checkbox
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
    const n = Math.floor(+f.elements.target.value || 0);
    const tags = f.elements.categories.value.split(",").map((s) => s.trim()).filter(Boolean);
    const parts = [n >= 2 ? `×${n}` : "", ...tags.map((c) => "#" + c)].filter(Boolean);
    $("#task-more-summary").text(parts.length ? "· " + parts.join(" · ") : "");
  }
  $form.on("input", '[name="target"], [name="categories"]', updateMoreSummary);

  // Live "Resets in …" preview while choosing a repeat. The server's core does the date maths.
  const REPEAT_FIELDS = '[name="kind"], [name="weekday"], [name="days"], [name="every"], [name="anchor"], [data-w]';

  // --- daily windows editor (times are minutes after the daily reset, shown as HH:MM) ----------

  const DAY_MIN = 24 * 60;
  const minutesToHhmm = (m) => `${String(Math.floor((m % DAY_MIN) / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
  function hhmmToMinutes(s) {
    const m = /^(\d{2}):(\d{2})$/.exec(s || "");
    return m ? +m[1] * 60 + +m[2] : null;
  }

  function addWindowRow(start, end) {
    $("#window-rows").append(`
      <li>
        <input type="time" data-w="start" value="${minutesToHhmm(start)}" aria-label="Opens">
        <span class="muted">–</span>
        <input type="time" data-w="end" value="${minutesToHhmm(end)}" aria-label="Closes">
        <button type="button" class="icon-btn" data-window-remove title="Remove window" aria-label="Remove window">${icon("close")}</button>
      </li>`);
  }

  // A new row repeats the pattern so far: the gap between the last two rows' openings (or, with one
  // row, starts where it ends) and the same length as the last row. Every row stays editable.
  $("#window-add").on("click", function () {
    const rows = $("#window-rows li").map(function () {
      const start = hhmmToMinutes($(this).find('[data-w="start"]').val());
      const end = hhmmToMinutes($(this).find('[data-w="end"]').val());
      return start === null || end === null ? null : [[start, end === 0 ? DAY_MIN : end]];
    }).get();
    const last = rows[rows.length - 1];
    if (!last) return addWindowRow(0, 60), schedulePreview();
    const length = last[1] - last[0];
    const step = rows.length >= 2 ? last[0] - rows[rows.length - 2][0] : length;
    const start = last[0] + (step > 0 ? step : length);
    if (start >= DAY_MIN) return toast("That would pass midnight; the day's windows repeat every day anyway.");
    addWindowRow(start, Math.min(start + length, DAY_MIN));
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

  $(document).on("click", "[data-filter]", function (e) {
    e.stopPropagation();
    const cat = this.dataset.filter;
    filter = cat && filter !== cat ? cat : null;
    renderFilters(); // highlight the chip right away; the narrowed view follows
    refresh();
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
      case "timer": {
        const input = prompt("Timer length (e.g. 10, 25m, 1h, 1:30, 90s)", "10m");
        if (input === null) return;
        const seconds = parseDuration(input);
        return seconds ? send({ type: "start_timer", task_id: t.id, seconds }) : toast("Couldn’t read that duration.");
      }
      case "timer-stop": return send({ type: "stop_timer", task_id: t.id });
      case "reminder": {
        const input = prompt("Remind at (e.g. 18:30) or in (e.g. 30m, 2h)", "1h");
        if (input === null) return;
        const at = parseWhen(input);
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
  if (hideDone) refresh(); // the page is rendered unfiltered; narrow it to what's left
});
