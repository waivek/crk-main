// Client-side rules in static/logic.js. Run: node --test tools/todo/tests/ (pytest runs it too,
// see test_logic_js.py).
const test = require("node:test");
const assert = require("node:assert/strict");
const L = require("../static/logic.js");

const NONE = { filter: null, hidden: [] };

test("esc escapes HTML", () => {
  assert.equal(L.esc(`<a href="x">Tom's & co</a>`), "&lt;a href=&quot;x&quot;&gt;Tom&#39;s &amp; co&lt;/a&gt;");
  assert.equal(L.esc(5), "5");
});

test("findTask looks in steps and opted-out tasks", () => {
  const bays = [
    { tasks: [{ id: "g", subtasks: [{ id: "s", subtasks: [] }] }], opted_out: [] },
    { tasks: [], opted_out: [{ id: "off", subtasks: [] }] },
  ];
  assert.equal(L.findTask(bays, "s").id, "s");
  assert.equal(L.findTask(bays, "off").id, "off");
  assert.equal(L.findTask(bays, "nope"), null);
});

test("fmtDuration rounds up to the second", () => {
  assert.equal(L.fmtDuration(1), "1s");
  assert.equal(L.fmtDuration(65_000), "1m 05s");
  assert.equal(L.fmtDuration(2 * 3600_000 + 5 * 60_000), "2h 05m 00s");
  assert.equal(L.fmtDuration(26 * 3600_000), "1d 2h 00m 00s");
});

test("parseDuration reads minutes, hours, seconds and h:mm", () => {
  assert.equal(L.parseDuration("10"), 600);
  assert.equal(L.parseDuration(" 25m "), 1500);
  assert.equal(L.parseDuration("25 min"), 1500);
  assert.equal(L.parseDuration("2H"), 7200);
  assert.equal(L.parseDuration("90s"), 90);
  assert.equal(L.parseDuration("1:30"), 5400);
  for (const bad of ["", null, "abc", "1:3", "1.5h", "-5", "1:60", "h", "m 5", "5m 3h"]) assert.equal(L.parseDuration(bad), null, bad);
});

test("parseDuration reads lengths copied from the game", () => {
  const H = 3600;
  assert.equal(L.parseDuration("10h 37m"), 10 * H + 37 * 60);
  assert.equal(L.parseDuration("10h37m"), 10 * H + 37 * 60);
  assert.equal(L.parseDuration("7H 48M"), 7 * H + 48 * 60);
  assert.equal(L.parseDuration("1h 5m 30s"), H + 330);
  assert.equal(L.parseDuration("2d 3h"), 2 * 86400 + 3 * H);
  assert.equal(L.parseDuration("37m 10s"), 37 * 60 + 10);
  assert.equal(L.parseDuration("10:37:00"), 10 * H + 37 * 60);
  assert.equal(L.parseDuration("0:00:45"), 45);
});

test("fmtTimeLeft writes a length parseDuration reads back", () => {
  assert.equal(L.fmtTimeLeft(45), "45s");
  assert.equal(L.fmtTimeLeft(0.2), "1s");
  assert.equal(L.fmtTimeLeft(60), "1m");
  assert.equal(L.fmtTimeLeft(61), "2m"); // rounds up
  assert.equal(L.fmtTimeLeft(10 * 3600 + 37 * 60), "10h 37m");
  assert.equal(L.fmtTimeLeft(3600), "1h");
  assert.equal(L.fmtTimeLeft(2 * 86400 + 3 * 3600), "2d 3h");
  for (const s of [45, 60, 3600, 38220, 2 * 86400 + 5 * 60]) assert.equal(L.parseDuration(L.fmtTimeLeft(s)), s);
});

test("timerFields: a name and the time left as hours + minutes", () => {
  assert.deepEqual(L.timerFields("  Mine Venture ", "10", "37"), { title: "Mine Venture", seconds: 38220 });
  assert.deepEqual(L.timerFields("Bell", "", "45"), { title: "Bell", seconds: 2700 });
  assert.deepEqual(L.timerFields("Bell", "2", ""), { title: "Bell", seconds: 7200 });
  assert.deepEqual(L.timerFields("Bell", "23", "59"), { title: "Bell", seconds: 23 * 3600 + 59 * 60 }); // the most
  assert.throws(() => L.timerFields("", "1", "0"), /Name the timer/);
  assert.throws(() => L.timerFields("Bell", "", ""), /Enter the time left/);
  assert.throws(() => L.timerFields("Bell", "0", "0"), /Enter the time left/);
  assert.throws(() => L.timerFields("Bell", "1.5", "0"), /whole numbers/);
  assert.throws(() => L.timerFields("Bell", "-1", "0"), /whole numbers/);
  assert.throws(() => L.timerFields("Bell", "24", "0"), /Hours can be at most 23/);
  assert.throws(() => L.timerFields("Bell", "0", "60"), /Minutes can be at most 59/);
  assert.throws(() => L.timerFields("Bell", "0", "90"), /Minutes can be at most 59/);
});

test("splitTimeLeft rounds up to the minute", () => {
  assert.deepEqual(L.splitTimeLeft(10 * 3600 + 36 * 60 + 20), { hours: 10, minutes: 37 });
  assert.deepEqual(L.splitTimeLeft(3600), { hours: 1, minutes: 0 });
  assert.deepEqual(L.splitTimeLeft(1), { hours: 0, minutes: 1 });
  assert.deepEqual(L.splitTimeLeft(-5), { hours: 0, minutes: 0 });
});

test("parseWhen: a duration from now, or a local clock time today else tomorrow", () => {
  const now = new Date(2026, 8, 29, 12, 0); // local time, so the test doesn't depend on the zone
  assert.equal(L.parseWhen("+30", now).getTime(), now.getTime() + 30 * 60_000);
  assert.equal(L.parseWhen("2h", now).getTime(), now.getTime() + 7200_000);
  assert.deepEqual(L.parseWhen("18:30", now), new Date(2026, 8, 29, 18, 30));
  assert.deepEqual(L.parseWhen("9:05", now), new Date(2026, 8, 30, 9, 5)); // already passed today
  assert.deepEqual(L.parseWhen("12:00", now), new Date(2026, 8, 30, 12, 0)); // exactly now: tomorrow
  assert.equal(L.parseWhen("soon", now), null);
});

test("refillFraction is clamped to 0..1", () => {
  assert.equal(L.refillFraction(1000, 1000, 0), 0);
  assert.equal(L.refillFraction(1000, 1000, 250), 0.25);
  assert.equal(L.refillFraction(1000, 1000, 5000), 1);
  assert.equal(L.refillFraction(9000, 1000, 0), 0);
});

test("the eye hides a tag and shows it again", () => {
  let s = NONE;
  assert.equal(L.tagState(s, "Ads"), "shown");
  s = L.toggleHideTag(s, "Ads");
  assert.deepEqual(s, { filter: null, hidden: ["Ads"] });
  assert.equal(L.tagState(s, "Ads"), "hidden");
  s = L.toggleHideTag(s, "Events");
  assert.deepEqual(s, { filter: null, hidden: ["Ads", "Events"] });
  s = L.toggleHideTag(s, "Ads");
  assert.deepEqual(s, { filter: null, hidden: ["Events"] });
});

test("hiding the 'only' tag drops that filter; hiding another keeps it", () => {
  assert.deepEqual(L.toggleHideTag({ filter: "Ads", hidden: [] }, "Ads"), { filter: null, hidden: ["Ads"] });
  assert.deepEqual(L.toggleHideTag({ filter: "Weekly", hidden: [] }, "Ads"), { filter: "Weekly", hidden: ["Ads"] });
});

test("the filter functions don't change the state they're given", () => {
  const s = Object.freeze({ filter: "Ads", hidden: Object.freeze(["Weekly"]) });
  L.toggleHideTag(s, "Ads");
  L.toggleHideTag(s, "Weekly");
  L.toggleOnlyTag(s, "Weekly");
  assert.deepEqual(s, { filter: "Ads", hidden: ["Weekly"] });
  assert.deepEqual(L.NO_FILTERS, NONE);
});

test("filterSummary spells out what's filtered", () => {
  const cats = ["Ads", "Events", "Weekly"];
  assert.equal(L.filterSummary(NONE, cats), null);
  assert.equal(L.filterSummary({ filter: "Weekly", hidden: [] }, cats), "Only #Weekly");
  assert.equal(L.filterSummary({ filter: null, hidden: ["Ads", "Events"] }, cats), "Hiding #Ads, #Events");
  assert.equal(L.filterSummary({ filter: "Weekly", hidden: ["Ads"] }, cats), "Only #Weekly · Hiding #Ads");
  assert.equal(L.filterSummary({ filter: null, hidden: ["Gone"] }, cats), null); // a deleted tag hides nothing
});

test("a tag on a task row toggles 'only' and un-hides it", () => {
  assert.deepEqual(L.toggleOnlyTag(NONE, "Ads"), { filter: "Ads", hidden: [] });
  assert.deepEqual(L.toggleOnlyTag({ filter: "Ads", hidden: [] }, "Ads"), NONE);
  assert.deepEqual(L.toggleOnlyTag({ filter: null, hidden: ["Ads", "X"] }, "Ads"), { filter: "Ads", hidden: ["X"] });
  assert.deepEqual(L.toggleOnlyTag({ filter: "Ads", hidden: ["X"] }, "Ads"), { filter: null, hidden: ["X"] });
});

test("viewQuery carries hidden tags, the one tag and hide-done", () => {
  assert.equal(L.viewQuery({ ...NONE, hideDone: false }), "");
  assert.equal(
    L.viewQuery({ filter: "Weekly", hidden: ["Ads", "Big & small"], hideDone: true }),
    "?hide_tag=Ads&hide_tag=Big+%26+small&category=Weekly&hide_done=1",
  );
});

test("hiddenNote says what the filters left out of a bay", () => {
  assert.equal(L.hiddenNote({ tasks: [{}], hidden_done: 0 }), null);
  assert.equal(L.hiddenNote({ tasks: [] }), null); // no filters: no counts at all
  assert.deepEqual(L.hiddenNote({ tasks: [], hidden_done: 3 }), { icon: "done_all", text: "All done · 3 done hidden" });
  assert.deepEqual(L.hiddenNote({ tasks: [{}], hidden_done: 3 }), { icon: "done_all", text: "3 done hidden" });
  assert.deepEqual(L.hiddenNote({ tasks: [], hidden_tagged: 2 }), { icon: "visibility_off", text: "2 hidden by tag" });
  // Something is hidden by tag, so the bay isn't necessarily "all done".
  assert.deepEqual(L.hiddenNote({ tasks: [], hidden_done: 3, hidden_tagged: 2 }),
    { icon: "done_all", text: "3 done hidden · 2 hidden by tag" });
});

test("task dialog: tags, counter target and the 'Counter & tags' summary", () => {
  assert.deepEqual(L.splitTags(" ads, ,Weekly ,"), ["ads", "Weekly"]);
  assert.deepEqual(L.splitTags(""), []);
  for (const [v, want] of [["", 0], ["1", 0], ["2", 2], ["5.7", 5], ["x", 0], ["-3", 0]]) assert.equal(L.counterTarget(v), want, v);
  assert.equal(L.moreSummary("5", "ads, pvp"), "· ×5 · #ads · #pvp");
  assert.equal(L.moreSummary("1", ""), "");
  assert.equal(L.moreSummary("", "ads"), "· #ads");
});

test("window times: HH:MM both ways, an end of 00:00 is midnight", () => {
  assert.equal(L.minutesToHhmm(0), "00:00");
  assert.equal(L.minutesToHhmm(150), "02:30");
  assert.equal(L.minutesToHhmm(L.DAY_MIN), "00:00");
  assert.equal(L.hhmmToMinutes("02:30"), 150);
  assert.equal(L.hhmmToMinutes("2:30"), null);
  assert.equal(L.hhmmToMinutes(""), null);
  assert.deepEqual(L.windowRange("12:00", "00:00"), [720, 1440]);
  assert.equal(L.windowRange("12:00", ""), null);
});

test("'Add window' repeats the pattern so far", () => {
  assert.deepEqual(L.nextWindow([]), [0, 60]);
  assert.deepEqual(L.nextWindow([[0, 720]]), [720, 1440]);          // Monster Menace: the second half
  assert.deepEqual(L.nextWindow([[0, 30], [120, 150]]), [240, 270]); // Post Office: every 2h for 30m
  assert.deepEqual(L.nextWindow([[0, 30], null]), [30, 60]);         // an unfinished row is skipped
  assert.deepEqual(L.nextWindow([[0, 30], [1380, 1440]]), null);     // would pass midnight
  assert.deepEqual(L.nextWindow([[1320, 1400]]), [1400, 1440]);      // clipped to the end of the day
});

test("recurrence: the repeat the dialog describes", () => {
  assert.deepEqual(L.recurrence({ kind: "daily" }), { kind: "daily" });
  assert.deepEqual(L.recurrence({ kind: "weekly", weekday: "2" }), { kind: "weekly", weekday: 2 });
  assert.deepEqual(L.recurrence({ kind: "weekly", weekday: "2", days: [3, 4] }), { kind: "weekly", weekday: 2, days: [3, 4] });
  assert.deepEqual(L.recurrence({ kind: "weekly", weekday: "2", days: [0, 1, 2, 3, 4, 5, 6] }), { kind: "weekly", weekday: 2 });
  assert.deepEqual(L.recurrence({ kind: "days_of_week", days: [5] }), { kind: "days_of_week", days: [5] });
  assert.throws(() => L.recurrence({ kind: "days_of_week", days: [] }), /at least one day/);
  assert.deepEqual(L.recurrence({ kind: "interval", every: "3", anchor: "2026-09-25" }), { kind: "interval", every: 3, anchor: "2026-09-25" });
  assert.throws(() => L.recurrence({ kind: "interval", every: "1", anchor: "2026-09-25" }), /at least 2/);
  assert.throws(() => L.recurrence({ kind: "interval", every: "3", anchor: "" }), /Pick a day/);
  assert.deepEqual(L.recurrence({ kind: "windows", windows: [["00:00", "12:00"], ["12:00", "00:00"]] }),
    { kind: "windows", windows: [[0, 720], [720, 1440]] });
  assert.throws(() => L.recurrence({ kind: "windows", windows: [] }), /at least one window/);
  assert.throws(() => L.recurrence({ kind: "windows", windows: [["00:00", ""]] }), /every window/);
});

test("cooldownFields: refill time, holds, current value and the optional next-one progress", () => {
  const base = { title: "Tickets", hours: "3", minutes: "0", capacity: "8", current: "5" };
  assert.deepEqual(L.cooldownFields(base), { title: "Tickets", minutes: 180, capacity: 8, value: 5 });
  assert.deepEqual(L.cooldownFields({ ...base, progressKind: "remaining", progressHours: "1", progressMinutes: "10" }),
    { title: "Tickets", minutes: 180, capacity: 8, value: 5, progress: { remaining: 70 } });
  assert.deepEqual(L.cooldownFields({ ...base, progressKind: "elapsed", progressHours: "", progressMinutes: "0" }).progress,
    { elapsed: 0 });
  assert.throws(() => L.cooldownFields({ ...base, hours: "0", minutes: "" }), /longer than 0/);
  assert.throws(() => L.cooldownFields({ ...base, progressKind: "remaining", progressHours: "3", progressMinutes: "0" }),
    /less than the refill time/);
});
