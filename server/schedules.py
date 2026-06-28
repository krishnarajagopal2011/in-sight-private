"""Life-screen engine: compute today's plan from the knowledge base.

Consumes the editable knowledge/*.yaml files (merged into cfg by config.py):
  fitness  -> today's sessions
  food     -> next meal, tonight's dal soak, daily macro totals, supplements
  house    -> today's chores grouped by time of day (daily + weekly + monthly)
  travel   -> upcoming trips

Everything here is pure: given a date it returns a plan. The browser then picks
the "next meal right now" and "is it past soak time" live, so the screen stays
current to the minute without re-running sync.
"""
from __future__ import annotations

import calendar
import datetime as dt
from typing import Any

WEEKDAYS = [
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
]
_ORDINALS = {"first": 0, "second": 1, "third": 2, "fourth": 3, "last": -1}


def _weekday_key(d: dt.date) -> str:
    return WEEKDAYS[d.weekday()]


def _minus_minutes(hhmm: str, minutes: int) -> str:
    try:
        h, m = (int(x) for x in str(hhmm).split(":")[:2])
    except ValueError:
        return hhmm
    total = (h * 60 + m - minutes) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def _supplements_today(food: dict[str, Any], meals: list) -> list[dict[str, Any]]:
    """Timed supplement/med reminders, anchored `minutes_before` each named meal's
    actual time today. A meal missing from today's plan (e.g. a fast day) is skipped."""
    out = []
    for s in food.get("supplements", []) or []:
        target = str(s.get("meal", "")).lower()
        mtime = next(
            (m.get("time") for m in meals if target and target in str(m.get("name", "")).lower()),
            None,
        )
        if not mtime:
            continue
        out.append({
            "name": f"Before {s.get('meal', 'meal').lower()}",
            "time": _minus_minutes(mtime, int(s.get("minutes_before", 30) or 30)),
            "items": [str(i) for i in s.get("items", []) or []],
        })
    out.sort(key=lambda x: x["time"])
    return out


# ── Fitness ──────────────────────────────────────────────────────────────────
def todays_fitness(cfg: dict[str, Any], d: dt.date) -> list[dict[str, Any]]:
    fitness = cfg.get("fitness", {}) or {}
    weekly = fitness.get("weekly", {}) or {}
    rotations = fitness.get("rotations", {}) or {}
    week = d.isocalendar().week
    items = []
    for s in weekly.get(_weekday_key(d), []) or []:
        start, end = s.get("start", ""), s.get("end", "")
        detail = s.get("detail", "")
        # A `rotate` key cycles through a named rotation list, one per week.
        rot = s.get("rotate")
        opts = rotations.get(rot) if rot else None
        if opts:
            choice = opts[week % len(opts)]
            detail = f"{choice} · {detail}" if detail else choice
        items.append(
            {
                "name": s.get("name", "Session"),
                "start": start,
                "end": end,
                "time_range": f"{start}–{end}" if start and end else (start or ""),
                "detail": detail,
            }
        )
    items.sort(key=lambda x: str(x.get("start", "99:99")))
    return items


# ── Food ─────────────────────────────────────────────────────────────────────
def food_today(cfg: dict[str, Any], d: dt.date) -> dict[str, Any]:
    food = cfg.get("food", {}) or {}
    health = cfg.get("health", {}) or {}
    phase = health.get("phase", 4)

    # Phase 1 = intensive kickstart (~1200 kcal). But on "creative days" not in
    # kickstart_days, fall back to the ~150g maintenance plan to protect focus.
    kickstart = food.get("kickstart", {}) or {}
    kickstart_days = health.get("kickstart_days", []) or []
    use_kickstart = (
        phase == 1
        and bool(kickstart.get("meals"))
        and (not kickstart_days or _weekday_key(d) in kickstart_days)
    )
    if use_kickstart:
        day = kickstart
        targets_override = kickstart.get("targets")
    else:
        days = food.get("days", {}) or {}
        variants = food.get("variants", {}) or {}
        wk = _weekday_key(d)
        if variants.get(wk):
            # This weekday rotates through several day-plans — pick one per week.
            vlist = variants[wk]
            day = vlist[d.isocalendar().week % len(vlist)] or {}
        else:
            day = days.get(wk, {}) or {}
            # Auto-fill any day with no meals from the days that DO have them —
            # cycling for variety — so every day shows a plan even if only a few
            # were entered. (Non-destructive: derived at read time, not written.)
            if not (day.get("meals") or []):
                filled = [k for k in WEEKDAYS if (days.get(k, {}) or {}).get("meals")]
                if filled:
                    day = days.get(filled[WEEKDAYS.index(wk) % len(filled)], {}) or {}
        targets_override = None

    meals = list(day.get("meals", []) or [])
    meals.sort(key=lambda m: str(m.get("time", "99:99")))
    supplements = _supplements_today(food, meals)

    totals = {"protein_g": 0, "carbs_g": 0, "fat_g": 0, "fiber_g": 0, "calories": 0}
    for m in meals:
        for k in totals:
            totals[k] += int(m.get(k, 0) or 0)

    return {
        "targets": targets_override or food.get("targets", {}) or {},
        "totals": totals,
        "phase": phase,
        "kickstart": use_kickstart,
        "fast": bool(day.get("fast")),
        "soak_by": food.get("soak_by", "19:00"),
        "soak_tonight": day.get("soak_tonight", ""),
        "prep_anchor": food.get("prep_anchor", ""),
        "supplements": supplements,
        "guidance": food.get("guidance", []) or [],
        "meals": meals,
    }


# ── House ────────────────────────────────────────────────────────────────────
def _monthly_due(item: dict[str, Any], d: dt.date) -> bool:
    when = str(item.get("when", "")).strip().lower()
    if when.startswith("day-"):
        try:
            return d.day == int(when.split("-", 1)[1])
        except ValueError:
            return False
    if "-" in when:
        ord_word, wd_word = when.split("-", 1)
        if ord_word not in _ORDINALS or wd_word not in WEEKDAYS:
            return False
        target_wd = WEEKDAYS.index(wd_word)
        # All dates in this month that fall on the target weekday.
        ndays = calendar.monthrange(d.year, d.month)[1]
        matches = [
            day for day in range(1, ndays + 1)
            if dt.date(d.year, d.month, day).weekday() == target_wd
        ]
        idx = _ORDINALS[ord_word]
        return bool(matches) and d.day == matches[idx]
    return False


def todays_house(cfg: dict[str, Any], d: dt.date) -> dict[str, Any]:
    house = cfg.get("house", {}) or {}
    sections_meta = house.get("sections", []) or [
        {"key": "early_morning", "label": "Early morning"},
        {"key": "afternoon_evening", "label": "Afternoon – evening"},
        {"key": "night", "label": "Night"},
    ]
    wk = _weekday_key(d)
    bucket: dict[str, list[str]] = {s["key"]: [] for s in sections_meta}

    def add(section: str, tasks: Any) -> None:
        if section not in bucket:
            bucket[section] = []
        for t in tasks or []:
            if t not in bucket[section]:
                bucket[section].append(t)

    # daily (only on configured working days)
    daily_days = house.get("daily_days", WEEKDAYS)
    if wk in daily_days:
        for section, tasks in (house.get("daily", {}) or {}).items():
            add(section, tasks)
    # weekly[today]
    for section, tasks in ((house.get("weekly", {}) or {}).get(wk, {}) or {}).items():
        add(section, tasks)
    # monthly items due today
    for item in house.get("monthly", []) or []:
        if _monthly_due(item, d):
            add(item.get("section", "early_morning"), [item.get("task", "")])

    sections = [
        {
            "key": s["key"],
            "label": s.get("label", s["key"]),
            "from": s.get("from", "00:00"),
            "to": s.get("to", "23:59"),
            "tasks": bucket.get(s["key"], []),
        }
        for s in sections_meta
    ]
    total = sum(len(s["tasks"]) for s in sections)
    return {"sections": sections, "total": total}


# ── Daily schedule (time blocks → pick-one-of-two) ───────────────────────────
def _parents_due(cfg: dict[str, Any], d: dt.date) -> str | None:
    pc = (cfg.get("schedule", {}) or {}).get("parents_call") or {}
    since = _as_date(pc.get("since"))
    if since is None or d < since:
        return None
    every = int(pc.get("every_days", 2) or 2)
    if every > 0 and (d - since).days % every == 0:
        return pc.get("label", "Call parents")
    return None


def _todays_events(cfg: dict[str, Any], d: dt.date) -> list[dict[str, Any]]:
    sched = cfg.get("schedule", {}) or {}
    wk = _weekday_key(d)
    return [
        e for e in (sched.get("events", []) or [])
        if wk in [str(x).lower() for x in (e.get("days") or [])]
    ]


def _overlaps(a0: str, a1: str, b0: str, b1: str) -> bool:
    # HH:MM strings compare correctly lexically.
    return a0 < (b1 or "23:59") and (b0 or "00:00") < (a1 or "23:59")


def todays_schedule(cfg: dict[str, Any], d: dt.date) -> dict[str, Any]:
    sched = cfg.get("schedule", {}) or {}
    events = _todays_events(cfg, d)
    parents = _parents_due(cfg, d)
    rot = d.toordinal()                     # rotate non-forced options by day
    out = []
    for b in sched.get("blocks", []) or []:
        start, end = b.get("start", ""), b.get("end", "")
        pool = [str(o) for o in (b.get("options", []) or [])]
        forced: list[str] = []
        if b.get("community"):
            for e in events:
                if _overlaps(e.get("start", "00:00"), e.get("end", "23:59"), start, end):
                    label = f"{e.get('name', 'Event')} · {e.get('start', '')}".strip(" ·")
                    forced.append(label)
            if parents:
                forced.append(parents)
        picks: list[str] = []
        for f in forced:                    # events / parents-call come first
            if f not in picks:
                picks.append(f)
            if len(picks) >= 2:
                break
        i = 0                               # then rotate-fill from the pool (varies daily)
        while len(picks) < 2 and pool and i <= 2 * len(pool):
            cand = pool[(rot + i) % len(pool)]
            if cand not in picks:
                picks.append(cand)
            i += 1
        out.append({
            "name": b.get("name", ""),
            "start": start,
            "end": end,
            "kind": b.get("kind", ""),
            "source": b.get("source", ""),
            "picks": picks[:2],
            "boundary": b.get("boundary", ""),
            "note": b.get("note", ""),
        })
    out.sort(key=lambda x: str(x.get("start", "99:99")))
    return {"blocks": out, "buffer_min": int(sched.get("buffer_min", 15) or 15)}


# ── Travel ───────────────────────────────────────────────────────────────────
def upcoming_travel(cfg: dict[str, Any], d: dt.date, limit: int = 3) -> list[dict[str, Any]]:
    travel = cfg.get("travel", {}) or {}
    trips = travel.get("trips", []) if isinstance(travel, dict) else (travel or [])
    rows = []
    for t in trips or []:
        start = _as_date(t.get("start"))
        end = _as_date(t.get("end")) or start
        if start is None or end is None or end < d:
            continue
        rows.append(
            {
                "destination": t.get("destination", ""),
                "start": start.isoformat(),
                "end": end.isoformat(),
                "days_until": (start - d).days,
                "ongoing": start <= d <= end,
                "note": t.get("note", ""),
                "purpose": t.get("purpose", ""),
            }
        )
    rows.sort(key=lambda r: r["start"])
    return rows[:limit]


# ── Assemble ─────────────────────────────────────────────────────────────────
def build_life_payload(cfg: dict[str, Any], d: dt.date) -> dict[str, Any]:
    return {
        "source": "knowledge",
        "date": d.isoformat(),
        "weekday": d.strftime("%A"),
        "fitness": todays_fitness(cfg, d),
        "food": food_today(cfg, d),
        "house": todays_house(cfg, d),
        "travel": upcoming_travel(cfg, d),
        "schedule": todays_schedule(cfg, d),
    }


def _as_date(v: Any) -> dt.date | None:
    if v is None:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None
