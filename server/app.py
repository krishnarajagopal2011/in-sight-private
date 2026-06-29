"""Flask server: serves the kiosk views, a tiny JSON API, and the phone log form.

The display API only ever reads snapshots from SQLite — it never calls dVerse on
the request path, so the kiosk is always instant. sync.py touches the network on a
schedule. The health block is the one exception: it's computed per-request so a
reading you just logged on your phone shows on the Life screen immediately.

Dev:   python -m server.app           (Flask dev server)
Prod:  served by waitress via scripts/refresh + systemd (see systemd/).
"""
from __future__ import annotations

import copy
import datetime as dt
import os
import threading
import time
from secrets import token_hex

from flask import Flask, jsonify, redirect, request, send_from_directory, session

from . import assistant, config, db, focus, health, schedules, sync
from .config import DB_PATH, KNOWLEDGE_DIR, KNOWLEDGE_FILES, WEB_DIR, load_config, server_settings

# Secrets the Settings page can store (whitelist — never echo values back).
ALLOWED_SECRETS = {
    "ANTHROPIC_API_KEY": "Anthropic API key (for the setup assistant)",
    "INSIGHT_DVERSE_PASSWORD": "dVerse central command password",
}

app = Flask(__name__, static_folder=None)

_CFG = load_config()
_SETTINGS = server_settings(_CFG)

# ── Cloud bring-up (no-ops for the local LAN kiosk) ──────────────────────────
# Make sure the DB/tables exist even when served by `waitress-serve server.app:app`
# (which never calls run()), so a fresh Render disk works on first boot.
db.init()
# If a durable Postgres (DATABASE_URL) is configured, restore /log history into the
# fresh ephemeral SQLite so health readings survive free-tier resets. No-op otherwise.
db.restore_readings_from_pg()

# Optional password gate — ON only when INSIGHT_PASSWORD is set (so a public URL
# isn't wide open). The local kiosk leaves it unset and stays frictionless.
app.secret_key = os.environ.get("INSIGHT_SECRET_KEY") or token_hex(32)
app.permanent_session_lifetime = dt.timedelta(days=30)
_PASSWORD = os.environ.get("INSIGHT_PASSWORD")
# Read-only access for the home-screen widget via ?token=... (so it loads without
# the login screen). Set INSIGHT_WIDGET_TOKEN to enable.
_WIDGET_TOKEN = os.environ.get("INSIGHT_WIDGET_TOKEN")
_OPEN_PATHS = {"/login", "/api/health", "/manifest.webmanifest", "/sw.js"}


@app.before_request
def _gate():
    if not _PASSWORD:
        return None                                  # auth disabled
    p = request.path
    if p in _OPEN_PATHS or p.startswith("/static/") or session.get("auth"):
        return None
    if _WIDGET_TOKEN and request.args.get("token") == _WIDGET_TOKEN:
        return None                                  # widget token (read-only views/API)
    if p.startswith("/api/"):
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return redirect("/login")


@app.get("/login")
def login_view():
    return send_from_directory(WEB_DIR, "login.html")


@app.post("/login")
def login_post():
    data = request.get_json(silent=True) or request.form
    if _PASSWORD and (data.get("password") or "") == _PASSWORD:
        session.permanent = True
        session["auth"] = True
        return redirect("/projects")
    return redirect("/login?e=1")


# In-process sync — for cloud hosts (Render) where a persistent disk can't be
# shared with a separate cron service. Runs an initial sync then loops. Gated by
# INSIGHT_BG_SYNC so the Pi/LAN box keeps using its own OS cron instead.
def _bg_sync_loop():
    interval = int(os.environ.get("INSIGHT_SYNC_SECONDS", "900"))
    while True:
        try:
            cfg = load_config()
            sync.sync_projects(cfg)
            sync.sync_life(cfg)
        except Exception:                            # noqa: BLE001 — never kill the thread
            pass
        time.sleep(interval)


if os.environ.get("INSIGHT_BG_SYNC", "").lower() in ("1", "true", "yes"):
    threading.Thread(target=_bg_sync_loop, name="bg-sync", daemon=True).start()


def _snapshot_body(name: str):
    snap = db.get_snapshot(name)
    if snap is None:
        return {
            "ok": False, "stale": True,
            "error": "no snapshot yet — run `python -m server.sync`",
            "settings": {"refresh_seconds": _SETTINGS["refresh_seconds"]},
        }, 503
    age = time.time() - snap["updated_at"]
    return {
        "ok": True,
        "stale": age > _SETTINGS["stale_after_seconds"],
        "age_seconds": round(age),
        "updated_at": snap["updated_at"],
        "settings": {"refresh_seconds": _SETTINGS["refresh_seconds"]},
        "data": snap["payload"],
    }, 200


@app.get("/api/health")           # kiosk liveness probe (used by the launchers)
def healthcheck():
    return jsonify({"ok": True, "ts": int(time.time() * 1000)})


@app.get("/api/projects")
def api_projects():
    body, status = _snapshot_body("projects")
    if body.get("ok"):
        cfg = load_config()
        now = dt.datetime.now()
        # Live focus state (food timing → suggest deep vs. light tasks).
        body["data"]["focus"] = focus.build_focus(cfg, now)
        # Past wind-down / before wake → don't push work; the screen goes to rest.
        body["data"]["rest"] = schedules.rest_now(cfg, now)
    return jsonify(body), status


@app.get("/api/life")
def api_life():
    # Free-tier cold start wipes the ephemeral snapshot — rebuild it on demand so the
    # screen isn't empty while waiting for the background sync.
    if db.get_snapshot("life") is None:
        try:
            sync.sync_life(load_config())
        except Exception:                          # noqa: BLE001 — never fail the request
            pass
    body, status = _snapshot_body("life")
    if body.get("ok"):
        # Live health block (reads latest phone-logged readings + current phase).
        cfg = load_config()
        body["data"]["health"] = health.build_health(cfg, dt.date.today(), DB_PATH)
        # Surface the top tasks here too, so the Life board has a Tasks card.
        proj = db.get_snapshot("projects")
        body["data"]["tasks"] = (proj["payload"].get("immediate") if proj else []) or []
        # Today's tap-to-done state, so checked items stay checked across refreshes.
        body["data"]["completions"] = db.get_completions(dt.date.today())
    return jsonify(body), status


@app.post("/api/complete")
def api_complete():
    """Toggle a checklist item done/undone for a day (tap-to-complete on the kiosk)."""
    body = request.get_json(silent=True) or {}
    key = (body.get("key") or "").strip()
    if not key:
        return jsonify({"ok": False, "error": "missing key"}), 400
    date = (body.get("date") or dt.date.today().isoformat())[:10]
    db.set_completion(date, key, bool(body.get("done")))
    return jsonify({"ok": True})


# ── Health logging (phone form) ──────────────────────────────────────────────
@app.post("/api/log")
def api_log():
    payload = request.get_json(silent=True) or request.form.to_dict()
    data = {"date": payload.get("date") or dt.date.today().isoformat()}
    for f in ("weight_kg", "waist_cm", "fasting_glucose", "post_meal_glucose",
              "hba1c_pct", "ketones"):
        v = payload.get(f)
        data[f] = float(v) if v not in (None, "", "null") else None
    data["post_meal_label"] = payload.get("post_meal_label") or None
    data["notes"] = payload.get("notes") or None
    rid = db.add_reading(data)
    return jsonify({"ok": True, "id": rid})


@app.get("/api/readings")
def api_readings():
    return jsonify({"ok": True, "readings": db.recent_readings(20)})


# ── AI setup assistant + admin ───────────────────────────────────────────────
@app.post("/api/assistant")
def api_assistant():
    body = request.get_json(silent=True) or {}
    messages = body.get("messages") or []
    api_key = body.get("api_key")  # bring-your-own; used per-request, never stored
    try:
        result = assistant.run_turn(messages, api_key=api_key)
        return jsonify({"ok": True, **result})
    except assistant.AssistantError as e:
        return jsonify({"ok": False, "error": str(e)}), 400


def _expand_food_week(data: dict) -> dict:
    """Fill every weekday's meals by cycling the days that have them, so all 7 days
    are written out explicitly (and independently editable)."""
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, dict):
        return data
    filled = [d for d in schedules.WEEKDAYS if (days.get(d) or {}).get("meals")]
    if not filled:
        return data
    for i, d in enumerate(schedules.WEEKDAYS):
        if not (days.get(d) or {}).get("meals"):
            days[d] = copy.deepcopy(days[filled[i % len(filled)]])
    data["days"] = {d: days[d] for d in schedules.WEEKDAYS if d in days}  # Mon→Sun order
    return data


@app.post("/api/assistant/apply")
def api_assistant_apply():
    """Write reviewed proposals into the knowledge base, then rebuild the snapshot."""
    body = request.get_json(silent=True) or {}
    written, errors = [], []
    import yaml
    for p in body.get("proposals") or []:
        domain = p.get("domain")
        if domain not in KNOWLEDGE_FILES:          # whitelist → no path traversal
            errors.append(f"unknown domain '{domain}'")
            continue
        try:
            if p.get("data") is not None:          # edited via the friendly form
                if domain == "food":
                    _expand_food_week(p["data"])   # write out all 7 days
                text = yaml.safe_dump(p["data"], sort_keys=False, allow_unicode=True)
            else:                                   # raw-YAML fallback
                text = p.get("yaml", "")
                yaml.safe_load(text)                # validate before writing
            (KNOWLEDGE_DIR / f"{domain}.yaml").write_text(text, encoding="utf-8")
            written.append(domain)
        except Exception as e:                      # noqa: BLE001 — surface to UI
            errors.append(f"{domain}: {e}")
    if written:
        try:
            sync.sync_life(load_config())           # reflect changes immediately
        except Exception:                           # noqa: BLE001
            pass
    return jsonify({"ok": not errors, "written": written, "errors": errors})


@app.get("/api/secrets")
def api_secrets_status():
    # Report only whether each secret is set — never the value.
    return jsonify({
        "ok": True,
        "secrets": {k: {"label": v, "set": bool(os.environ.get(k))}
                    for k, v in ALLOWED_SECRETS.items()},
    })


@app.post("/api/secrets")
def api_secrets_set():
    """Save secrets to the gitignored config/.env (chmod 600), applied live."""
    body = request.get_json(silent=True) or {}
    saved = []
    for key in ALLOWED_SECRETS:               # whitelist — ignore anything else
        val = (body.get(key) or "").strip()
        if val:
            config.set_env_secret(key, val)
            saved.append(key)
    return jsonify({"ok": bool(saved), "saved": saved})


@app.get("/settings")
def settings_view():
    return send_from_directory(WEB_DIR, "settings.html")


@app.get("/setup")
def setup_view():
    return send_from_directory(WEB_DIR, "setup.html")


@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "life.html")


@app.get("/projects")
def projects_view():
    # One unified view now — /projects serves it directly (NOT a redirect, which
    # the service worker can't return for a navigation → ERR_FAILED).
    return send_from_directory(WEB_DIR, "life.html")


@app.get("/life")
def life_view():
    return send_from_directory(WEB_DIR, "life.html")


@app.get("/widget")               # compact home-screen widget view (token-gated)
def widget_view():
    return send_from_directory(WEB_DIR, "widget.html")


@app.get("/log")                  # phone-friendly logging form
def log_view():
    return send_from_directory(WEB_DIR, "log.html")


@app.get("/static/<path:path>")
def static_files(path: str):
    return send_from_directory(WEB_DIR / "static", path)


# ── PWA (installable full-screen phone app) ──────────────────────────────────
@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(WEB_DIR, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    # Served from root so its scope covers /life and /projects.
    resp = send_from_directory(WEB_DIR, "sw.js", mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def run():
    db.init()
    app.run(host=_SETTINGS["host"], port=_SETTINGS["port"], debug=False)


if __name__ == "__main__":
    run()
