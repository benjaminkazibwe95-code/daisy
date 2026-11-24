"""Daisy media engine for the Flask app. Registered in app.py. Adds:
  GET  /media/thumbs/<file>, /media/full/<file>   images the engine stored
  POST /api/media/resolve                         question -> cards (called by the UI after the answer shows)
  POST /api/media/feedback, /api/media/tap/<id>   thumbs up/down, tap counts
  admin (needs the /console?key=ADMIN_KEY session): dashboard, add link, kill switch, run a job now"""
import os, re, threading, time
from datetime import date
from flask import Blueprint, jsonify, request, send_from_directory, session, abort
from media_engine import db, auto, scheduler, ingest

bp = Blueprint("media_bp", __name__)
db.init()
DATA_DIR = os.path.abspath(db.DATA_DIR)
ITEM_ID = re.compile(r"^[a-z]{3}_[0-9a-f]{8}$")

_hits, _lock = {}, threading.Lock()
def _too_fast(limit=30, window=60):
    """Small per-IP limiter so nobody can burn the YouTube quota through this route."""
    ip = (request.headers.get("X-Forwarded-For", request.remote_addr) or "?").split(",")[0].strip()
    now = time.time()
    with _lock:
        recent = [t for t in _hits.get(ip, []) if now - t < window]
        recent.append(now)
        _hits[ip] = recent
        if len(_hits) > 5000:
            _hits.clear()
    return len(recent) > limit

def _is_admin():
    return bool(os.environ.get("ADMIN_KEY")) and session.get("admin") is True

def start():
    scheduler.start()

FILE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}\.webp$")

@bp.route("/media/<folder>/<name>")
def media_file(folder, name):
    # only plain .webp names from two image folders: no paths, no '..', never the database or config
    if folder not in ("thumbs", "full") or not FILE_NAME.match(name):
        abort(404)
    resp = send_from_directory(os.path.join(DATA_DIR, folder), name)
    resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"   # file names are unique
    return resp

@bp.route("/api/media/resolve", methods=["POST"])
def resolve():
    if _too_fast():
        return jsonify({"blocks": []})
    d = request.get_json(silent=True) or {}
    mode = d.get("data_mode") if d.get("data_mode") in ("text", "thumbs", "full") else "thumbs"
    return jsonify({"blocks": auto.media_for(str(d.get("question", ""))[:500], mode)})

@bp.route("/api/media/feedback", methods=["POST"])
def feedback():
    d = request.get_json(silent=True) or {}
    ids = [i for i in (d.get("item_ids") or []) if isinstance(i, str) and ITEM_ID.match(i)][:5]
    col = "helpful" if d.get("helpful") else "unhelpful"
    if ids and not _too_fast(60):
        with db.conn() as c:
            for i in ids:
                c.execute(f"UPDATE items SET {col}={col}+1 WHERE id=?", (i,))
    return jsonify({"ok": True})

@bp.route("/api/media/tap/<item_id>", methods=["POST"])
def tap(item_id):
    if ITEM_ID.match(item_id) and not _too_fast(120):
        with db.conn() as c:
            c.execute("UPDATE items SET taps=taps+1 WHERE id=?", (item_id,))
    return jsonify({"ok": True})

# ---------- admin ----------
@bp.route("/api/media/admin/dashboard")
def dashboard():
    if not _is_admin():
        abort(403)
    today = date.today().isoformat()
    with db.conn() as c:
        q = lambda sql, *a: [dict(r) for r in c.execute(sql, a)]
        return jsonify({
            "counts": q("SELECT tier,kind,COUNT(*) n FROM items WHERE status='active' GROUP BY tier,kind"),
            "top_cards": q("SELECT id,title,kind,tier,taps,helpful,unhelpful FROM items ORDER BY taps DESC LIMIT 10"),
            "top_misses": q("SELECT query,kind,count FROM misses ORDER BY count DESC LIMIT 20"),
            "auto_disabled": q("SELECT id,title,note FROM items WHERE status='disabled' ORDER BY created_at DESC LIMIT 20"),
            "expired": q("SELECT id,title,review_by FROM items WHERE review_by IS NOT NULL AND review_by<? AND status='active'", today),
            "jobs": q("SELECT name,last_run FROM jobs"),
        })

@bp.route("/api/media/admin/ingest-link", methods=["POST"])
def ingest_link():
    if not _is_admin():
        abort(403)
    d = request.get_json(silent=True) or {}
    try:
        info = auto._run(ingest.from_link(str(d.get("url", ""))))
    except Exception as e:
        return jsonify({"error": f"could not ingest: {e}"}), 400
    item_id = db.add_item(tags=str(d.get("tags", "")), lang=str(d.get("lang", "en")),
                          review_by=d.get("review_by"), tier="curated", **info)
    return jsonify({"id": item_id, "title": info["title"], "credit": info["credit"]})

@bp.route("/api/media/admin/item/<item_id>/<action>", methods=["GET", "POST"])
def item_action(item_id, action):
    if not _is_admin():
        abort(403)
    status = {"disable": "disabled", "enable": "active"}.get(action)
    if not status or not ITEM_ID.match(item_id):
        abort(400)
    with db.conn() as c:
        c.execute("UPDATE items SET status=? WHERE id=?", (status, item_id))
    return jsonify({"id": item_id, "status": status})

@bp.route("/api/media/admin/run/<job>", methods=["GET", "POST"])
def run_now(job):
    if not _is_admin() or job not in scheduler.JOBS:
        abort(403)
    threading.Thread(target=scheduler.run_job, args=(job,), daemon=True).start()
    return jsonify({"started": job})
