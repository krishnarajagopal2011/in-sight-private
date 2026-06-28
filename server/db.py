"""Offline-first snapshot cache (SQLite).

The whole point: the display reads the *last good snapshot*, so a failed sync or a
dead network never blanks the screen — it just goes stale. sync.py writes
snapshots; app.py reads them.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from .config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    name        TEXT PRIMARY KEY,   -- 'projects' | 'life'
    payload     TEXT NOT NULL,      -- JSON blob the API serves verbatim
    updated_at  REAL NOT NULL       -- epoch seconds of last successful build
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS health_readings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    date        TEXT NOT NULL,          -- YYYY-MM-DD the reading is for
    weight_kg          REAL,
    waist_cm           REAL,
    fasting_glucose    REAL,            -- mg/dL
    post_meal_glucose  REAL,            -- mg/dL
    post_meal_label    TEXT,            -- which meal (e.g. "Lunch")
    hba1c_pct          REAL,
    ketones            REAL,
    notes              TEXT,
    created_at  REAL NOT NULL
);
"""

_READING_FIELDS = [
    "weight_kg", "waist_cm", "fasting_glucose", "post_meal_glucose",
    "post_meal_label", "hba1c_pct", "ketones", "notes",
]

# Optional durable store for /log health readings (a free Postgres like Neon). When
# DATABASE_URL is set, each logged reading is ALSO written to Postgres, and on startup
# the local (ephemeral) SQLite is restored from it — so history survives the free
# tier's disk resets. Reads still hit local SQLite, so the screen never queries
# Postgres on its 30s poll. Unset = pure local SQLite, exactly as before.
DATABASE_URL = os.environ.get("DATABASE_URL")

_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS health_readings (
    id SERIAL PRIMARY KEY,
    date TEXT NOT NULL,
    weight_kg DOUBLE PRECISION,
    waist_cm DOUBLE PRECISION,
    fasting_glucose DOUBLE PRECISION,
    post_meal_glucose DOUBLE PRECISION,
    post_meal_label TEXT,
    hba1c_pct DOUBLE PRECISION,
    ketones DOUBLE PRECISION,
    notes TEXT,
    created_at DOUBLE PRECISION NOT NULL
);
"""


def _pg_connect():
    import psycopg2  # lazy import: only needed when DATABASE_URL is set
    return psycopg2.connect(DATABASE_URL)


def _pg_init() -> None:
    conn = _pg_connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(_PG_SCHEMA)
    finally:
        conn.close()


def _pg_add_reading(date: str, vals: list, created: float) -> None:
    conn = _pg_connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO health_readings (date, {', '.join(_READING_FIELDS)}, created_at) "
                f"VALUES (%s, {', '.join(['%s'] * len(_READING_FIELDS))}, %s)",
                (date, *vals, created),
            )
    finally:
        conn.close()


def restore_readings_from_pg(db_path: Path = DB_PATH) -> int:
    """Rebuild local SQLite health_readings from Postgres (called at startup so a
    fresh ephemeral disk regains its history). Best-effort; no-op without DATABASE_URL."""
    if not DATABASE_URL:
        return 0
    try:
        _pg_init()
        conn = _pg_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT date, {', '.join(_READING_FIELDS)}, created_at "
                    "FROM health_readings ORDER BY id"
                )
                rows = cur.fetchall()
        finally:
            conn.close()
        init(db_path)
        with _connect(db_path) as sconn:
            sconn.execute("DELETE FROM health_readings")
            sconn.executemany(
                f"INSERT INTO health_readings (date, {', '.join(_READING_FIELDS)}, created_at) "
                f"VALUES (?, {', '.join(['?'] * len(_READING_FIELDS))}, ?)",
                rows,
            )
            sconn.commit()
        return len(rows)
    except Exception:  # noqa: BLE001 — never block startup on the optional DB
        return 0


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path: Path = DB_PATH) -> None:
    with _connect(db_path) as conn:
        conn.executescript(_SCHEMA)


def set_snapshot(name: str, payload: dict[str, Any], db_path: Path = DB_PATH) -> None:
    init(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO snapshots (name, payload, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(name) DO UPDATE SET payload=excluded.payload, "
            "updated_at=excluded.updated_at",
            (name, json.dumps(payload), time.time()),
        )
        conn.commit()


def get_snapshot(name: str, db_path: Path = DB_PATH) -> Optional[dict[str, Any]]:
    """Return {'payload': ..., 'updated_at': float} or None if never built."""
    init(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT payload, updated_at FROM snapshots WHERE name = ?", (name,)
        ).fetchone()
    if row is None:
        return None
    return {"payload": json.loads(row["payload"]), "updated_at": row["updated_at"]}


def set_meta(key: str, value: str, db_path: Path = DB_PATH) -> None:
    init(db_path)
    with _connect(db_path) as conn:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        conn.commit()


def get_meta(key: str, default: Optional[str] = None, db_path: Path = DB_PATH) -> Optional[str]:
    init(db_path)
    with _connect(db_path) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


# ── Health readings (logged from the phone form) ─────────────────────────────
def add_reading(data: dict[str, Any], db_path: Path = DB_PATH) -> int:
    """Insert one health reading. `data` may include date + any of _READING_FIELDS.
    Mirrors to Postgres when DATABASE_URL is set, so the reading survives a reset."""
    init(db_path)
    date = str(data.get("date") or "")[:10]
    vals = [data.get(f) for f in _READING_FIELDS]
    created = time.time()
    with _connect(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO health_readings (date, {', '.join(_READING_FIELDS)}, created_at) "
            f"VALUES (?, {', '.join(['?'] * len(_READING_FIELDS))}, ?)",
            (date, *vals, created),
        )
        conn.commit()
        rid = cur.lastrowid
    if DATABASE_URL:
        try:
            _pg_add_reading(date, vals, created)
        except Exception:  # noqa: BLE001 — local write already succeeded; don't fail the log
            pass
    return rid


def recent_readings(limit: int = 30, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    init(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM health_readings ORDER BY date DESC, id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def latest_reading(field: str, db_path: Path = DB_PATH) -> Optional[dict[str, Any]]:
    """Most recent non-null value for one field, with its date."""
    if field not in _READING_FIELDS:
        return None
    init(db_path)
    with _connect(db_path) as conn:
        row = conn.execute(
            f"SELECT {field} AS value, date FROM health_readings "
            f"WHERE {field} IS NOT NULL ORDER BY date DESC, id DESC LIMIT 1"
        ).fetchone()
    return {"value": row["value"], "date": row["date"]} if row else None
