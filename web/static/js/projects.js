// Screen 1 — from dVerse central command:
//   focus = #1 priority · Top priorities (myTasks) · Waiting on you (drafts) ·
//   the user's milestones (team progress).
import { startClock, poll, scheduleDailyReload, initPWA, el, clear } from "./common.js";

const dot = document.getElementById("dot");
startClock(document.getElementById("time"), document.getElementById("date"));
scheduleDailyReload();
initPWA();

const STATUS_LABEL = { TODO: "To do", IN_PROGRESS: "In progress", DONE: "Done", BLOCKED: "Blocked" };

function priorityPill(label) {
  const key = (label || "").toLowerCase();
  const cls = key === "high" ? "high" : key === "low" ? "low" : "normal";
  return el("span", `pill ${cls}`, label || "Normal");
}

function renderFocusState(f) {
  const box = document.getElementById("focus-state");
  if (!box) return;
  clear(box);
  if (!f || !f.state) return;
  const band = el("div", `focus-state ${f.state}`);
  const left = el("div", "fs-left");
  left.appendChild(el("span", "fs-label", f.label));
  band.appendChild(left);
  // timing hint on the right
  let hint = "";
  if (f.state === "prime" && f.minutes_left != null) hint = `${f.minutes_left} min left`;
  else if (f.next_window_in != null) hint = `deep work in ${f.next_window_in} min`;
  if (hint) band.appendChild(el("div", "fs-hint", hint));
  box.appendChild(band);
}

function render(data) {
  renderFocusState(data.focus);

  // ONLY "what to do now": the top 2 priorities as a pick-one-of-two. The rest of
  // the backlog/milestones is deliberately NOT shown — it surfaces in the Life work
  // block, two at a time by priority.
  const focus = document.getElementById("focus");
  clear(focus);
  const opts = (data.immediate || []).slice(0, 2);
  if (opts.length) {
    focus.appendChild(el("div", "focus-kicker", opts.length > 1 ? "Do one of these next" : "Do this next"));
    const list = el("div", "do-options");
    for (const t of opts) {
      const row = el("div", "do-option");
      row.appendChild(el("div", "focus-title", t.title));
      const meta = el("div", "focus-meta");
      meta.append(t.goal || "");
      if (t.milestone) meta.append(`  ›  ${t.milestone}`);
      row.appendChild(meta);
      const pill = el("div"); pill.style.marginTop = ".5rem";
      pill.appendChild(priorityPill(t.priority_label));
      row.appendChild(pill);
      list.appendChild(row);
    }
    focus.appendChild(list);
  } else {
    focus.appendChild(el("div", "focus-kicker", "All clear"));
    focus.appendChild(el("div", "focus-title", "No priorities right now 🎉"));
  }
}

poll("/api/projects", render, dot);
