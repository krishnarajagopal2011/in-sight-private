"""The 3am job (and boot job): build both snapshots into SQLite.

Run:  python -m server.sync           # build both
      python -m server.sync projects  # build one
      python -m server.sync life

Offline-first contract: if dVerse is unreachable, the projects snapshot is left
untouched (the display keeps the last good data and goes "stale"). The life
snapshot is computed purely from local config, so it always rebuilds.
"""
from __future__ import annotations

import datetime as dt
import sys
import time

from . import db, dverse, gcal, health, schedules
from .config import load_config


def sync_projects(cfg: dict) -> bool:
    """Pull from central command and write the projects snapshot. Returns success."""
    dv = cfg.get("dverse", {}) or {}
    provider = dv.get("provider", "dverse")

    if provider == "local":
        payload = _local_projects_payload(cfg)
        db.set_snapshot("projects", payload)
        print("[sync] projects: built from local fallback")
        return True

    base = dv.get("base_url", dverse.DEFAULT_BASE_URL)
    email = dv.get("email")
    password = dv.get("password")
    if not email or not password:
        print("[sync] projects: no dVerse credentials; keeping last snapshot")
        return False

    try:
        data = dverse.fetch_central_data(base, email, password)
        payload = dverse.build_projects_payload(data)
        db.set_snapshot("projects", payload)
        db.set_meta("projects_last_ok", str(time.time()))
        print(
            f"[sync] projects: {len(payload['projects'])} projects, "
            f"{len(payload['immediate'])} immediate tasks"
        )
        return True
    except dverse.DverseError as e:
        # Do NOT overwrite the snapshot — keep showing the last good data.
        print(f"[sync] projects: FAILED ({e}); keeping last snapshot")
        return False


def sync_life(cfg: dict) -> bool:
    today = dt.date.today()
    payload = schedules.build_life_payload(cfg, today)
    payload["health"] = health.build_health(cfg, today)

    # Appointments from Google Calendar (two accounts). Offline-first: if the
    # fetch fails, reuse the last good calendar so the screen never blanks.
    try:
        payload["calendar"] = gcal.fetch_events(cfg)
    except gcal.GCalError as e:
        prev = db.get_snapshot("life")
        payload["calendar"] = (prev or {}).get("payload", {}).get("calendar", []) if prev else []
        print(f"[sync] calendar: FAILED ({e}); kept {len(payload['calendar'])} cached events")

    # Multi-day calendar events become trips, merged with knowledge/travel.yaml.
    try:
        cal_trips = gcal.ics_trips(cfg)
        if cal_trips:
            seen = {(t.get("destination"), t.get("start")) for t in payload["travel"]}
            extra = [t for t in cal_trips if (t.get("destination"), t.get("start")) not in seen]
            payload["travel"] = sorted(payload["travel"] + extra, key=lambda t: t.get("start", ""))[:5]
    except Exception:  # noqa: BLE001 — trips are a bonus; never fail the sync
        pass

    db.set_snapshot("life", payload)
    t = payload["food"]["totals"]
    print(
        f"[sync] life: {payload['weekday']} — "
        f"{len(payload['fitness'])} fitness, {len(payload['food']['meals'])} meals "
        f"({t['protein_g']}P/{t['carbs_g']}C/{t['fat_g']}F/{t['calories']}kcal), "
        f"{payload['house']['total']} house tasks, {len(payload['travel'])} trips, "
        f"{len(payload.get('calendar', []))} appts"
    )
    return True


def _local_projects_payload(cfg: dict) -> dict:
    """Build a projects snapshot from the editable knowledge base (no dVerse account).

    Source: knowledge/projects.yaml (cfg['projects']), with legacy config
    projects_fallback / immediate_fallback as a fallback.
    """
    pk = cfg.get("projects", {}) or {}
    projects_src = pk.get("projects") or cfg.get("projects_fallback", []) or []
    tasks_src = pk.get("tasks") or cfg.get("immediate_fallback", []) or []

    projects = []
    for p in projects_src:
        projects.append(
            {
                "title": p.get("title", "Untitled"),
                "owner": p.get("owner"),
                "status": p.get("status", "ACTIVE"),
                "pct": p.get("pct", 0),
                "done": p.get("done", 0),
                "total": p.get("total", 0),
                "milestone": p.get("milestone") or p.get("next_action"),
                "milestone_status": p.get("milestone_status"),
                "milestone_done": 0,
                "milestone_total": 0,
                "next_action": p.get("next_action") or p.get("milestone"),
                "next_action_priority": p.get("priority"),
            }
        )
    immediate = [
        {
            "title": t.get("title", ""),
            "priority": None,
            "priority_label": t.get("priority"),
            "goal": t.get("goal"),
            "milestone": None,
            "due": t.get("due"),
            "status": None,
        }
        for t in tasks_src
    ]
    # Surface projects as "milestones" too (the screen's main grid).
    milestones = [
        {"goal": p["title"], "title": p.get("milestone") or p.get("next_action"),
         "status": p.get("status"), "done": 0, "total": 0, "pct": p.get("pct", 0), "due": None}
        for p in projects_src
    ]
    return {
        "source": "local",
        "person": cfg.get("person"),
        "projects": projects,
        "immediate": immediate,
        "milestones": milestones,
        "waiting": {"count": 0, "items": []},
    }


def main(argv: list[str]) -> int:
    cfg = load_config()
    db.init()
    targets = argv or ["projects", "life"]
    ok = True
    if "projects" in targets:
        ok = sync_projects(cfg) and ok
    if "life" in targets:
        ok = sync_life(cfg) and ok
    db.set_meta("last_sync", str(time.time()))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
