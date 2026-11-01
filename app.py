"""
Daisy — app.py
Built from scratch. No external "Daisy API" and no outside AI service —
this IS Daisy. She answers from her own brain (brain.py, which reads the
dictionary in processing-law-ai.jsx) and serves the index.html /
console.html UIs.

ENV VARS to set on your host (Render, etc.):
  SECRET_KEY           = any random string
  DAISY_CRAWLER        = optional, set to 1 to let Daisy keep learning from
                          Wikipedia in the background (daisy_ingest.py)
  DATABASE_URL         = optional Postgres URL — falls back to local SQLite
                          (daisy.db) if unset, so this runs with zero setup
  DAISY_API_KEY         = shared secret — TrustedBiz's /api/daisy/publish
                          must be sent this exact value to accept a publish
  TRUSTEDBIZ_API_URL   = https://trustedbiz.co.ug   (no trailing slash)
"""

import os, re, json, sqlite3, secrets, string
from datetime import datetime
from functools import wraps
from pathlib import Path

import requests
import brain
import harmony_layer
from flask import Flask, request, session, jsonify, render_template, redirect, Response, stream_with_context, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash

# ── APP ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

DAISY_API_KEY       = os.environ.get("DAISY_API_KEY", "")
TRUSTEDBIZ_API_URL = os.environ.get("TRUSTEDBIZ_API_URL", "").rstrip("/")

# ── DB (SQLite by default, zero setup — swap in Postgres via DATABASE_URL
#    later the same way TrustedBiz's app.py does, if this needs to scale) ──
DB_PATH = os.environ.get("DAISY_DB_PATH", str(Path(__file__).parent / "daisy.db"))

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, email TEXT UNIQUE, password TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS projects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, owner_id INTEGER, share_code TEXT UNIQUE,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS project_members (
        project_id INTEGER, user_id INTEGER,
        PRIMARY KEY (project_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS chats (
        id TEXT PRIMARY KEY,
        project_id INTEGER, owner_id INTEGER,
        title TEXT, messages TEXT DEFAULT '[]',
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS stats (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        words INTEGER DEFAULT 0, ingest_cycles INTEGER DEFAULT 0,
        last_ingest TEXT, log_tail TEXT DEFAULT '[]'
    );
    """)
    conn.execute("INSERT OR IGNORE INTO stats (id, words, ingest_cycles, log_tail) VALUES (1,0,0,'[]')")
    conn.commit()
    conn.close()

init_db()

def q(sql):  # keeps '?' placeholders — kept as a hook if you swap to Postgres later
    return sql

def db_execute(sql, params=()):
    conn = get_db()
    conn.execute(sql, params)
    conn.commit()
    conn.close()

def db_fetchone(sql, params=()):
    conn = get_db()
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row

def db_fetchall(sql, params=()):
    conn = get_db()
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows

def db_insert(sql, params=()):
    conn = get_db()
    cur = conn.execute(sql, params)
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return rid

def bump_stats(words_added, log_line=None):
    row = db_fetchone("SELECT * FROM stats WHERE id=1")
    log = json.loads(row["log_tail"] or "[]") if row else []
    if log_line:
        log.append(f"{datetime.utcnow().isoformat(timespec='seconds')}Z — {log_line}")
        log = log[-30:]
    db_execute(
        "UPDATE stats SET words=words+?, ingest_cycles=ingest_cycles+1, "
        "last_ingest=?, log_tail=? WHERE id=1",
        (words_added, datetime.utcnow().isoformat(timespec="seconds") + "Z", json.dumps(log))
    )

# ── AUTH HELPERS ─────────────────────────────────────────────────────────
def current_user():
    uid = session.get("user_id")
    if not uid:
        return None
    row = db_fetchone("SELECT id, name, email FROM users WHERE id=?", (uid,))
    return dict(row) if row else None

def login_required(f):
    @wraps(f)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return jsonify({"error": "login required"}), 401
        return f(*a, **kw)
    return wrapper

# ── PAGES ─────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return render_template("index.html")

@app.route("/console")
def console():
    return render_template("console.html")

@app.route("/welcome")
def welcome():
    return render_template("landing.html")

@app.route("/privacy")
def privacy():
    return render_template("privacy.html")

@app.route("/terms")
def terms():
    return render_template("terms.html")

# ── STATIC / PWA / SEO FILES ─────────────────────────────────────────────
# These all physically live in templates/ in this project (there's no
# separate static/ folder), and the pages above reference them at a couple
# of different paths (/icons/..., /static/icons/..., /manifest.json,
# /sw.js, the Google verification file, Android's assetlinks.json) — none
# of that was wired up when app.py got rewritten, so every one of these
# was a silent 404 until now.
TEMPLATES_DIR = str(Path(__file__).parent / "templates")

@app.route("/manifest.json")
def manifest():
    return send_from_directory(TEMPLATES_DIR, "manifest.json", mimetype="application/manifest+json")

@app.route("/sw.js")
def service_worker():
    return send_from_directory(TEMPLATES_DIR, "sw.js", mimetype="application/javascript")

@app.route("/icons/<path:filename>")
@app.route("/static/icons/<path:filename>")
def icons(filename):
    return send_from_directory(TEMPLATES_DIR, filename)

@app.route("/google2c13209b099aea62.html")
def google_verification():
    return send_from_directory(TEMPLATES_DIR, "google2c13209b099aea62.html")

@app.route("/.well-known/assetlinks.json")
def assetlinks():
    return send_from_directory(TEMPLATES_DIR, "assetlinks.json", mimetype="application/json")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        row = db_fetchone("SELECT * FROM users WHERE email=?", (email,))
        if row and check_password_hash(row["password"], password):
            session["user_id"] = row["id"]
            return redirect(request.args.get("next") or "/")
        return render_template("login.html", error="Wrong email or password.")
    return render_template("login.html", error=None)

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()[:100]
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        if not email or len(password) < 6:
            return render_template("register.html", error="Email and a 6+ char password are required.")
        if db_fetchone("SELECT id FROM users WHERE email=?", (email,)):
            return render_template("register.html", error="That email is already registered.")
        uid = db_insert(
            "INSERT INTO users (name, email, password) VALUES (?,?,?)",
            (name, email, generate_password_hash(password))
        )
        session["user_id"] = uid
        return redirect(request.args.get("next") or "/")
    return render_template("register.html", error=None)

@app.route("/auth/me")
def auth_me():
    return jsonify({"user": current_user()})

@app.route("/auth/logout", methods=["POST"])
def auth_logout():
    session.pop("user_id", None)
    return jsonify({"success": True})

# ── DAISY STATUS (console + settings "stats" panel) ─────────────────────
@app.route("/daisy/status")
def daisy_status():
    row = db_fetchone("SELECT * FROM stats WHERE id=1")
    return jsonify({
        "status": "online",
        "words": brain.word_count(),
        "ingest_cycles": row["ingest_cycles"] if row else 0,
        "last_ingest": row["last_ingest"] if row else None,
        "log_tail": json.loads(row["log_tail"] or "[]") if row else [],
    })

# ── THE CORE: /ask ────────────────────────────────────────────────────────
# Daisy answers from her own brain (brain.py). No outside AI service is
# called anywhere in this file.
def sse_line(obj):
    return json.dumps(obj) + "\n"

@app.route("/ask", methods=["POST"])
def ask():
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    history  = data.get("history") or []
    image    = data.get("image")
    has_image = bool(image and image.get("data"))

    def generate():
        yield sse_line({"event": "status", "status": "thinking"})
        try:
            result = brain.think(question, has_image=has_image)
        except Exception as e:
            print(f"[ask] brain error: {e}")
            yield sse_line({"event": "final", "answer": "Something went wrong inside my brain. Try asking again."})
            return

        answer = result["answer"]
        source = result["source"]
        try:
            if source in ("dictionary", "synthesis"):
                answer = harmony_layer.enhance_response(answer, source, history)
            harmony_layer.log_conversation(
                question, None if source == "unknown" else answer, source
            )
        except Exception:
            pass

        bump_stats(len(answer.split()), f"/ask answered from brain (source={source})")
        yield sse_line({
            "event": "final",
            "answer": answer,
            "source": source,
            "sources": [],
            "used_web_search": False,
            "memory_fact": None,
        })

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")


# ── NEURAL VOICE (free, no API key) ───────────────────────────────────────
# Uses the open-source `edge-tts` package. Daisy's browser falls back to the
# phone's own voice automatically if this route ever fails.
import asyncio
from functools import lru_cache

DEFAULT_VOICE = os.environ.get("DAISY_VOICE", "en-GB-SoniaNeural")
ALLOWED_VOICES = {
    "en-GB-SoniaNeural", "en-GB-LibbyNeural", "en-US-AvaNeural", "en-US-AriaNeural",
    "en-KE-AsiliaNeural", "en-TZ-ImaniNeural", "sw-KE-ZuriNeural", "sw-TZ-RehemaNeural",
    "en-GB-RyanNeural", "en-KE-ChilembaNeural",
}

@lru_cache(maxsize=300)
def _tts_bytes(text, voice):
    import edge_tts
    async def run():
        buf = bytearray()
        async for chunk in edge_tts.Communicate(text, voice).stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        return bytes(buf)
    return asyncio.run(run())

@app.route("/tts", methods=["POST"])
def tts():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:600]
    voice = data.get("voice") or DEFAULT_VOICE
    if voice not in ALLOWED_VOICES:
        voice = DEFAULT_VOICE
    if not text:
        return ("", 400)
    try:
        audio = _tts_bytes(text, voice)
    except Exception as e:
        print(f"[tts] failed: {e}")
        return ("", 502)
    if not audio:
        return ("", 502)
    return Response(audio, mimetype="audio/mpeg", headers={"Cache-Control": "private, max-age=3600"})

# ── CORRECTIONS (console "Knowledge" tab — teach Daisy answers) ──────────
@app.route("/api/corrections", methods=["GET", "POST", "DELETE"])
def api_corrections():
    corr = brain.load_corrections()
    if request.method == "GET":
        return jsonify([{"question": k, "answer": v} for k, v in corr.items()])
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "question required"}), 400
    if request.method == "POST":
        answer = (data.get("answer") or "").strip()
        if not answer:
            return jsonify({"error": "answer required"}), 400
        corr[question] = answer
    else:
        corr.pop(question, None)
    brain.save_corrections(corr)
    return jsonify({"success": True})

# ── EXPORT TO PDF ────────────────────────────────────────────────────────
@app.route("/export/pdf", methods=["POST"])
def export_pdf():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "Daisy").strip()[:120]
    content = data.get("content") or ""

    from fpdf import FPDF  # fpdf2

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 10, title)
    pdf.ln(2)
    pdf.set_font("Helvetica", size=11)
    for raw_line in content.split("\n"):
        line = raw_line.rstrip()
        if line.startswith("### "):
            pdf.set_font("Helvetica", "B", 12); pdf.multi_cell(0, 8, line[4:]); pdf.set_font("Helvetica", size=11)
        elif line.startswith("## "):
            pdf.set_font("Helvetica", "B", 13); pdf.multi_cell(0, 9, line[3:]); pdf.set_font("Helvetica", size=11)
        elif line.startswith("# "):
            pdf.set_font("Helvetica", "B", 15); pdf.multi_cell(0, 10, line[2:]); pdf.set_font("Helvetica", size=11)
        elif not line:
            pdf.ln(3)
        else:
            pdf.multi_cell(0, 7, line)

    out = bytes(pdf.output())
    safe = re.sub(r"[^\w\- ]+", "", title).strip().replace(" ", "-") or "daisy-answer"
    return Response(out, mimetype="application/pdf",
                     headers={"Content-Disposition": f'attachment; filename="{safe}.pdf"'})

# ── PROJECTS (shared workspaces via share code) ──────────────────────────
def gen_share_code():
    chars = string.ascii_uppercase + string.digits
    for _ in range(50):
        code = "".join(secrets.choice(chars) for _ in range(6))
        if not db_fetchone("SELECT id FROM projects WHERE share_code=?", (code,)):
            return code
    return secrets.token_hex(3).upper()

def project_to_dict(row):
    return {"id": row["id"], "name": row["name"], "share_code": row["share_code"]}

@app.route("/api/projects", methods=["GET", "POST"])
@login_required
def api_projects():
    uid = session["user_id"]
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        name = (data.get("name") or "Untitled project").strip()[:100]
        code = gen_share_code()
        pid = db_insert("INSERT INTO projects (name, owner_id, share_code) VALUES (?,?,?)", (name, uid, code))
        db_execute("INSERT OR IGNORE INTO project_members (project_id, user_id) VALUES (?,?)", (pid, uid))
        return jsonify(project_to_dict(db_fetchone("SELECT * FROM projects WHERE id=?", (pid,))))

    rows = db_fetchall("""
        SELECT p.* FROM projects p
        JOIN project_members m ON m.project_id = p.id
        WHERE m.user_id = ? ORDER BY p.created_at DESC
    """, (uid,))
    return jsonify([project_to_dict(r) for r in rows])

@app.route("/api/projects/join", methods=["POST"])
@login_required
def api_projects_join():
    uid = session["user_id"]
    code = (request.get_json(silent=True) or {}).get("code", "").strip().upper()
    row = db_fetchone("SELECT * FROM projects WHERE share_code=?", (code,))
    if not row:
        return jsonify({"error": "not found"}), 404
    db_execute("INSERT OR IGNORE INTO project_members (project_id, user_id) VALUES (?,?)", (row["id"], uid))
    return jsonify(project_to_dict(row))

def require_member(project_id, uid):
    return db_fetchone(
        "SELECT 1 FROM project_members WHERE project_id=? AND user_id=?", (project_id, uid)
    ) is not None

@app.route("/api/projects/<int:project_id>", methods=["GET"])
@login_required
def api_project_detail(project_id):
    uid = session["user_id"]
    if not require_member(project_id, uid):
        return jsonify({"error": "not a member"}), 403
    chats = db_fetchall(
        "SELECT id, title FROM chats WHERE project_id=? ORDER BY updated_at DESC", (project_id,)
    )
    return jsonify({"chats": [dict(c) for c in chats]})

@app.route("/api/projects/<int:project_id>/chats/<chat_id>", methods=["GET", "PUT"])
@login_required
def api_project_chat(project_id, chat_id):
    uid = session["user_id"]
    if not require_member(project_id, uid):
        return jsonify({"error": "not a member"}), 403

    if request.method == "PUT":
        data = request.get_json(silent=True) or {}
        title = (data.get("title") or "Untitled")[:120]
        messages = data.get("messages") or []
        existing = db_fetchone("SELECT id FROM chats WHERE id=?", (chat_id,))
        if existing:
            db_execute(
                "UPDATE chats SET title=?, messages=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (title, json.dumps(messages), chat_id)
            )
        else:
            db_execute(
                "INSERT INTO chats (id, project_id, owner_id, title, messages) VALUES (?,?,?,?,?)",
                (chat_id, project_id, uid, title, json.dumps(messages))
            )
        return jsonify({"success": True})

    row = db_fetchone("SELECT * FROM chats WHERE id=? AND project_id=?", (chat_id, project_id))
    if not row:
        return jsonify({"error": "not found"}), 404
    return jsonify({"id": row["id"], "title": row["title"], "messages": json.loads(row["messages"] or "[]")})

# ── PUBLISH TO TRUSTEDBIZ ─────────────────────────────────────────────────
@app.route("/publish/trustedbiz", methods=["POST"])
def publish_trustedbiz():
    """Called by index.html's "Publish to TrustedBiz" button. Runs
    server-side so DAISY_API_KEY never reaches the browser."""
    if not TRUSTEDBIZ_API_URL or not DAISY_API_KEY:
        return jsonify({"error": "Publishing is not configured yet on this server."}), 503

    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    html = data.get("html") or ""
    if not name:
        return jsonify({"error": "A business name is required."}), 400
    if not html or len(html) < 200:
        return jsonify({"error": "No finished site to publish."}), 400

    # If the owner_email matches an existing Daisy account, forward that
    # account's password hash (never the plaintext) so TrustedBiz can set
    # it as the login for a brand-new TrustedBiz account with the same
    # email. Since both apps hash with werkzeug's generate_password_hash,
    # the same hash verifies correctly on either side — so the person can
    # sign in to TrustedBiz with the exact email/password they already
    # use on Daisy, no separate claim step needed. TrustedBiz only uses
    # this for accounts it's creating for the first time; it never
    # touches the password of a TrustedBiz account that already exists.
    owner_email = (data.get("owner_email") or "").strip().lower()
    owner_password_hash = ""
    if owner_email:
        urow = db_fetchone("SELECT password FROM users WHERE email=?", (owner_email,))
        if urow:
            owner_password_hash = urow["password"] or ""

    try:
        resp = requests.post(
            f"{TRUSTEDBIZ_API_URL}/api/daisy/publish",
            json={
                "name": name,
                "category": data.get("category", ""),
                "description": data.get("description", ""),
                "whatsapp": data.get("whatsapp", ""),
                "brand_color": data.get("brand_color", ""),
                "owner_email": data.get("owner_email", ""),
                "owner_name": data.get("owner_name", ""),
                "owner_password_hash": owner_password_hash,
                "html": html,
            },
            headers={"Authorization": f"Bearer {DAISY_API_KEY}"},
            timeout=30,
        )
        return jsonify(resp.json()), resp.status_code
    except requests.exceptions.Timeout:
        return jsonify({"error": "TrustedBiz took too long to respond. Try again."}), 504
    except Exception as e:
        print(f"[publish/trustedbiz] error: {e}")
        return jsonify({"error": "Could not reach TrustedBiz right now."}), 502


# ── OPTIONAL: let Daisy keep learning (off unless DAISY_CRAWLER=1) ────────
if os.environ.get("DAISY_CRAWLER") == "1":
    try:
        import daisy_ingest
        daisy_ingest.init_daisy(app)
    except Exception as e:
        print(f"[DAISY] crawler not started: {e}")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
