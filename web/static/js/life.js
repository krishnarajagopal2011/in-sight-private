// Screen 2 — life, as a calm "right now / next 2 hours" view.
// The 3am snapshot (refreshed every ~15 min for calendar) carries the whole day;
// this script shows ONLY what's relevant to the current clock, re-checked every
// minute. If nothing falls in the next 2 hours, it keeps the NEXT upcoming item
// on screen so the display is never empty.
import { startClock, poll, scheduleDailyReload, initPWA, el, clear } from "./common.js";

const WINDOW_MIN = 120;   // look-ahead window — "within 2 hours from now"
const GRACE_MIN = 15;     // keep a thing visible briefly after its time passes

const dot = document.getElementById("dot");
startClock(document.getElementById("time"), document.getElementById("date"));
scheduleDailyReload();
initPWA();

let latest = null;

const nowMin = () => { const n = new Date(); return n.getHours() * 60 + n.getMinutes(); };
const toMin = (hhmm) => { const [h, m] = (hhmm || "0:0").split(":").map(Number); return h * 60 + (m || 0); };
const ongoing = (a, b) => { const n = nowMin(); return toMin(a) <= n && n <= toMin(b); };
const minsUntil = (t) => toMin(t) - nowMin();

function relTime(t) {
  const d = minsUntil(t);
  if (d <= 0) return "now";
  if (d < 60) return `in ${d} min`;
  return `in ${Math.round(d / 60 * 10) / 10} h`;
}

function panel(kicker, builder, opts = {}) {
  const p = el("section", `panel ${opts.focus ? "focus" : ""} ${opts.due ? "due" : ""}`);
  if (opts.accent) p.style.borderLeft = `6px solid ${opts.accent}`;
  if (kicker) {
    const k = el("div", "panel-kicker", kicker);
    if (opts.due || opts.focus) k.classList.add("hot");
    p.appendChild(k);
  }
  builder(p);
  return p;
}

// ── Unified timed-event model (fitness, meals, calendar) ─────────────────────
// Each event: {start (HH:MM), end?, live, allDay, sort, make(prefix) -> panel}
function collectEvents(data) {
  const ev = [];

  for (const s of data.fitness || []) {
    const live = ongoing(s.start, s.end);
    ev.push({
      start: s.start, end: s.end, live, allDay: false, sort: toMin(s.start),
      make: (prefix) => panel(prefix ?? (live ? "Movement · now" : `Movement · ${relTime(s.start)}`), (p) => {
        const line = el("div", "panel-title");
        line.appendChild(el("span", "accent", `${s.time_range}  `));
        line.append(s.name);
        p.appendChild(line);
        if (s.detail) p.appendChild(el("div", "panel-sub", s.detail));
      }, { due: live }),
    });
  }

  // Health nudges: glucose logs (morning + post-meal) and electrolyte reminders.
  for (const n of data.health?.nudges || []) {
    const due = minsUntil(n.time) <= 0;
    const kicker = n.kind === "electrolyte"
      ? (due ? "Electrolytes · now" : `Electrolytes · ${relTime(n.time)}`)
      : (due ? "Log now" : `Log · ${relTime(n.time)}`);
    ev.push({
      start: n.time, end: null, live: false, allDay: false, sort: toMin(n.time),
      make: (prefix) => panel(prefix ?? kicker, (p) => {
        p.appendChild(el("div", "panel-title", `${n.icon || "🩸"} ${n.label}`));
        if (n.sub) p.appendChild(el("div", "panel-sub", n.sub));
      }, { due }),
    });
  }

  const verb = data.food?.fast ? "Hydrate" : "Eat";   // water-fast day → hydration
  for (const m of data.food?.meals || []) {
    const due = minsUntil(m.time) <= 0;
    // Meals have no end time, so they're never "live" indefinitely — visibility
    // is governed purely by the window check (within GRACE after / WINDOW before).
    ev.push({
      start: m.time, end: null, live: false, allDay: false, sort: toMin(m.time),
      make: (prefix) => panel(prefix ?? (due ? `${verb} now` : `${verb} ${relTime(m.time)}`), (p) => {
        p.appendChild(el("div", "panel-title-lg", m.name));
        p.appendChild(el("div", "panel-time", m.time));
        const ul = el("ul", "clean meal-items");
        for (const it of m.items || []) ul.appendChild(el("li", null, it));
        p.appendChild(ul);
        if (m.note) p.appendChild(el("div", "meal-note", m.note));
      }, { focus: true, due }),
    });
  }

  // Supplement / med doses — timed 30 min before breakfast & dinner.
  for (const s of data.food?.supplements || []) {
    const due = minsUntil(s.time) <= 0;
    ev.push({
      start: s.time, end: null, live: false, allDay: false, sort: toMin(s.time),
      make: (prefix) => panel(prefix ?? (due ? "Supplements · now" : `Supplements · ${relTime(s.time)}`), (p) => {
        p.appendChild(el("div", "panel-title", `💊 ${s.name}`));
        p.appendChild(el("div", "panel-time", s.time));
        const ul = el("ul", "clean meal-items");
        for (const it of s.items || []) ul.appendChild(el("li", null, it));
        p.appendChild(ul);
        p.appendChild(el("div", "meal-note", "30 min before · with water"));
      }, { due }),
    });
  }

  // Meal prep — exactly what to prep, at the right time (decision-free).
  for (const mp of data.food?.meal_prep || []) {
    const due = minsUntil(mp.time) <= 0;
    ev.push({
      start: mp.time, end: null, live: false, allDay: false, sort: toMin(mp.time),
      make: (prefix) => panel(prefix ?? (due ? "Prep · now" : `Prep · ${relTime(mp.time)}`), (p) => {
        p.appendChild(el("div", "panel-title", `🔪 ${mp.title}`));
        const ul = el("ul", "clean meal-items");
        for (const it of mp.items || []) ul.appendChild(el("li", null, it));
        p.appendChild(ul);
      }, { due }),
    });
  }

  for (const c of data.calendar || []) {
    if (c.all_day) {
      ev.push({
        start: "00:00", end: "23:59", live: true, allDay: true, sort: -1,
        make: () => panel(`${c.label} · all day`, (p) => {
          p.appendChild(el("div", "panel-title", c.summary));
          if (c.location) p.appendChild(el("div", "panel-sub", `📍 ${c.location}`));
        }, { accent: c.color }),
      });
      continue;
    }
    const live = ongoing(c.start_hm, c.end_hm || c.start_hm);
    ev.push({
      start: c.start_hm, end: c.end_hm, live, allDay: false, sort: toMin(c.start_hm),
      make: (prefix) => panel(prefix ?? `${c.label} · ${live ? "now" : relTime(c.start_hm)}`, (p) => {
        p.appendChild(el("div", "panel-title", c.summary));
        const when = c.end_hm ? `${c.start_hm}–${c.end_hm}` : c.start_hm;
        p.appendChild(el("div", "panel-time", when));
        if (c.location) p.appendChild(el("div", "panel-sub", `📍 ${c.location}`));
      }, { due: live, accent: c.color }),
    });
  }
  return ev;
}

const inWindow = (e) => e.live || (() => { const d = minsUntil(e.start); return d <= WINDOW_MIN && d >= -GRACE_MIN; })();

// House is block-based (a time-of-day window), not a point event.
function housePanel(data) {
  const active = (data.house?.sections || []).filter((s) => s.tasks.length && ongoing(s.from, s.to));
  if (!active.length) return null;
  return panel("House · now", (p) => {
    for (const sec of active) {
      const ul = el("ul", "clean meal-items");
      for (const task of sec.tasks) ul.appendChild(el("li", null, task));
      p.appendChild(ul);
    }
  });
}

// Dal-soak: relevant only in the ~2 hours before soak time, through the evening.
function soakPanel(data) {
  const f = data.food || {};
  if (!f.soak_tonight) return null;
  const by = toMin(f.soak_by || "19:00");
  if (nowMin() < by - WINDOW_MIN) return null;
  const due = nowMin() >= by;
  return panel(due ? `Soak now · by ${f.soak_by}` : `Prep · soak ${relTime(f.soak_by)}`, (p) => {
    p.appendChild(el("div", "panel-title", `🫘 ${f.soak_tonight}`));
  }, { due });
}

function renderHealthStrip() {
  const strip = document.getElementById("health-strip");
  if (!strip) return;
  clear(strip);
  const h = latest?.health;
  if (!h || !h.phase_label) return;

  const bar = el("div", "health-strip");

  // Phase · week
  const ph = el("div", "hchip phase");
  ph.appendChild(el("span", "hchip-k", "Phase"));
  ph.appendChild(el("span", "hchip-v", `${h.phase_label}${h.week ? ` · wk ${h.week}` : ""}`));
  bar.appendChild(ph);

  // Weight progress
  const w = h.progress?.weight;
  if (w?.target) {
    const c = el("div", "hchip");
    c.appendChild(el("span", "hchip-k", "Weight"));
    c.appendChild(el("span", "hchip-v", `${w.current} → ${w.target} kg`));
    const bar2 = el("div", "hbar"); const sp = el("span"); sp.style.width = `${w.pct}%`; bar2.appendChild(sp);
    c.appendChild(bar2);
    bar.appendChild(c);
  } else if (w?.current) {
    const c = el("div", "hchip");
    c.appendChild(el("span", "hchip-k", "Weight"));
    c.appendChild(el("span", "hchip-v", `${w.current} kg`));
    bar.appendChild(c);
  }

  // HbA1c
  const a = h.progress?.hba1c;
  if (a?.current != null) {
    const c = el("div", "hchip");
    c.appendChild(el("span", "hchip-k", "HbA1c"));
    c.appendChild(el("span", "hchip-v", `${a.current}%${a.target ? ` → ${a.target}%` : ""}`));
    bar.appendChild(c);
  }

  // Next checkpoint — only on the day itself (action-step only, no future countdown).
  if (h.next_checkpoint && h.next_checkpoint.days_until <= 0) {
    const c = el("div", "hchip");
    c.appendChild(el("span", "hchip-k", "Next"));
    c.appendChild(el("span", "hchip-v", `${h.next_checkpoint.label} · today`));
    bar.appendChild(c);
  }

  strip.appendChild(bar);
}

// Tasks card — the top priority (from the Projects source). Not time-bound, so it
// shows whenever there's a task. Action-step only.
function tasksPanel(data) {
  const tasks = data.tasks || [];
  if (!tasks.length) return null;
  const top = tasks[0];
  const more = tasks.length - 1;
  return panel("Tasks · do next", (p) => {
    p.appendChild(el("div", "panel-title", top.title));
    if (top.goal) p.appendChild(el("div", "panel-sub", top.goal));
    if (more > 0) p.appendChild(el("div", "panel-sub", `+${more} more on Projects`));
  });
}

// Block card — flips to the CURRENT schedule block and shows TWO options (pick the
// one that pulls you). In a switch-buffer it shows the transition; otherwise null
// so the Tasks card (dVerse top priority) takes over.
function blockPanel(data) {
  const sched = data.schedule;
  if (!sched || !(sched.blocks || []).length) return null;
  const n = nowMin();
  const cur = sched.blocks.find((b) => b.start && b.end && toMin(b.start) <= n && n < toMin(b.end));
  if (cur) {
    let picks = cur.picks || [];
    if (cur.source === "dverse") picks = (data.tasks || []).slice(0, 2).map((t) => t.title);
    const kicker = picks.length > 1 ? `${cur.name} · pick one` : cur.name;
    return panel(kicker, (p) => {
      const ul = el("ul", "clean meal-items");
      for (const opt of picks) ul.appendChild(el("li", null, opt));
      p.appendChild(ul);
      if (cur.note) p.appendChild(el("div", "meal-note", cur.note));
      if (cur.boundary) p.appendChild(el("div", "meal-note", cur.boundary));
    }, { focus: true });
  }
  // Switch buffer: within buffer_min before the next block starts.
  const buf = sched.buffer_min || 15;
  const next = sched.blocks.filter((b) => toMin(b.start) > n).sort((a, b) => toMin(a.start) - toMin(b.start))[0];
  if (next) {
    const mins = toMin(next.start) - n;
    if (mins > 0 && mins <= buf) {
      return panel(`Switch · ${mins} min`, (p) => {
        p.appendChild(el("div", "panel-title", "Task-switch buffer"));
        p.appendChild(el("div", "panel-sub", `Next: ${next.name} at ${next.start}`));
      });
    }
  }
  return null;
}

// Groceries — the weekly shopping checklist, only on the configured shopping day.
function groceriesPanel(data) {
  const g = data.groceries;
  const cats = (g && g.due && g.categories) || null;
  if (!cats || !Object.keys(cats).length) return null;
  return panel("Groceries · shopping today", (p) => {
    for (const [cat, items] of Object.entries(cats)) {
      p.appendChild(el("div", "panel-sub", cat));
      const ul = el("ul", "clean meal-items");
      for (const it of items || []) ul.appendChild(el("li", null, it));
      p.appendChild(ul);
    }
  });
}

function render() {
  if (!latest) return;
  renderHealthStrip();
  const flow = document.getElementById("flow");
  clear(flow);

  const events = collectEvents(latest);
  const allDay = events.filter((e) => e.allDay);
  const timed = events.filter((e) => !e.allDay);

  const windowed = timed.filter(inWindow).sort((a, b) => a.sort - b.sort);
  const house = housePanel(latest);
  const soak = soakPanel(latest);
  const groceries = groceriesPanel(latest);
  // The current schedule block (with its 2 options) takes the Tasks slot; outside
  // any block it falls back to the dVerse top priority.
  const tasks = blockPanel(latest) || tasksPanel(latest);

  // Category cards, skipping any with nothing relevant: Fitness/Food (windowed),
  // House (current block), Tasks (top priority), plus soak/health nudges.
  const panels = [
    ...allDay.map((e) => e.make()),
    ...windowed.map((e) => e.make()),
    ...(house ? [house] : []),
    ...(tasks ? [tasks] : []),
    ...(groceries ? [groceries] : []),
    ...(soak ? [soak] : []),
  ];

  // Never go quiet: if nothing is in-window, keep the NEXT upcoming item on screen.
  if (!windowed.length && !house) {
    const future = timed.filter((e) => minsUntil(e.start) > 0).sort((a, b) => a.sort - b.sort);
    if (future.length) {
      const e = future[0];
      panels.push(e.make(`Next up · ${relTime(e.start)}`));
    } else if (!panels.length) {
      panels.push(panel("All clear", (p) => {
        p.appendChild(el("div", "panel-title-lg", "Nothing left today 🌿"));
      }, { focus: true }));
    }
  }

  flow.classList.toggle("centered", panels.length <= 2);
  flow.classList.toggle("single", panels.length === 1);
  panels.forEach((p) => flow.appendChild(p));
}

poll("/api/life", (data) => { latest = data; render(); }, dot);
setInterval(render, 60 * 1000); // re-filter against the clock every minute
