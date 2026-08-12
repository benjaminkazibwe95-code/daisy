"""
Daisy — app.py
Built from scratch. No external "Daisy API" — this IS Daisy. It calls the
real Anthropic API directly and serves the index.html / console.html UIs.

ENV VARS to set on your host (Render, etc.):
  SECRET_KEY           = any random string
  ANTHROPIC_API_KEY    = sk-ant-...                  (console.anthropic.com)
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
from flask import Flask, request, session, jsonify, render_template, redirect, Response, stream_with_context
from werkzeug.security import generate_password_hash, check_password_hash

# ── APP ───────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
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
    online = bool(ANTHROPIC_API_KEY)
    return jsonify({
        "status": "online" if online else "offline",
        "words": row["words"] if row else 0,
        "ingest_cycles": row["ingest_cycles"] if row else 0,
        "last_ingest": row["last_ingest"] if row else None,
        "log_tail": json.loads(row["log_tail"] or "[]") if row else [],
    })

# ── THE CORE: /ask ────────────────────────────────────────────────────────
DAISY_SYSTEM = """You are Daisy, a friendly AI assistant for busy, mostly
non-technical business owners. You chat naturally, and you can also build
and publish complete, working websites — nothing about this should ever
require the person to know code, pick a hosting provider, or configure
anything themselves.

WHEN TO BUILD (OR CHANGE) A SITE
If the person is asking you to build, design, create, update, fix, add to,
or polish a website, landing page, or site — whether that's the first
request or a follow-up edit on one you already built — reply with ONLY
one fenced code block in this exact form, nothing else: no preamble, no
bullet list of what changed, no clarifying questions, no explanation.

```html:site.html
<!DOCTYPE html>
... a complete, self-contained, polished single-page site with inline
    CSS and JS, real content matching what they asked for ...
```

Never split a build or an edit into an explanation followed by the code.
The code block IS the entire answer — for the very first build and for
every edit after it ("add a booking form", "clean this up", etc). Always
output the FULL updated file, never a diff or a snippet.

BUILD QUALITY — NOTHING SHOULD LOOK UNFINISHED
- Every section needs real, visible content. Never leave an empty div, an
  image placeholder with no photo, or a box reserved "for later" — those
  render as blank, broken-looking gaps on the actual page. If you don't
  have a real photo to use, don't create a spot for one at all: build
  that section with strong typography, color, and layout instead.
- Structure the site to fit the business, skipping what doesn't apply:
  hero (name + one clear line on what they do), about, what they offer
  (services or menu), hours, location, one obvious way to contact them.
  Skip anything you don't have real content for rather than padding it
  with filler or empty markup.
- Any form (booking, reservation, contact) must work with zero setup:
  point it at a mailto: link, or — if a WhatsApp number was given — a
  wa.me link pre-filled with the form's fields. Never leave a button that
  does nothing, and never ask which backend or hosting platform to wire
  it to. The person has none, and doesn't need one.
- Write like the business itself would — plain, specific language. No
  "Welcome to our website, we provide quality services to all our
  esteemed customers," no lorem ipsum, no AI-demo look (no gradient-blob
  backgrounds, no robot mascots, no glowing-button overload).
- Most visitors are on a phone in Uganda, often on limited data —
  mobile-first, single-column-friendly, fast, with a clear WhatsApp
  contact button near the top if a number was given.

GOING LIVE — ALWAYS TRUSTEDBIZ, NEVER ANYTHING ELSE
Right after you hand back a site, the interface automatically shows a
"Publish to TrustedBiz" button on it — don't mention it in that reply.
If the person asks how to publish, host, deploy, or get the site online
— before or after building — the answer is always the same: click
"Publish to TrustedBiz" on the file. It asks for the business name (the
only required field), plus optional category, WhatsApp number, and
email, and goes live instantly and for free at
businessname.trustedbiz.co.ug — no manual review, no setup. Never
suggest or ask about Vercel, Netlify, Nginx, a Node.js backend,
Formspree, or any other hosting or deployment option — TrustedBiz is the
only path this product offers, and the person has no infrastructure of
their own to deploy to. If they haven't built anything yet and ask about
this, tell them to describe the business or site they want first.

For every other message, just talk normally — no code fence, no filename,
plain conversational text."""

_FILE_FENCE_RE = re.compile(r"```html:([^\n]+)\n(.*?)```", re.DOTALL)

def detect_file(answer):
    m = _FILE_FENCE_RE.search(answer or "")
    return m.group(1).strip() if m else None

def looks_like_site_request(question):
    return bool(re.search(
        r"\b(website|web page|webpage|landing page|site for|homepage)\b",
        question or "", re.IGNORECASE
    ))

MODEL_MAP = {
    # Daisy's own brand names -> real Anthropic model IDs. Adjust these to
    # whatever's live on your account; kept in one place on purpose.
    "beni":  "claude-sonnet-4-6",
    "beni2": "claude-sonnet-4-6",
}

def sse_line(obj):
    return json.dumps(obj) + "\n"

@app.route("/ask", methods=["POST"])
def ask():
    if not ANTHROPIC_API_KEY:
        def _err():
            yield sse_line({"event": "final", "answer": "Daisy isn't configured yet — ANTHROPIC_API_KEY is missing on the server."})
        return Response(_err(), mimetype="application/x-ndjson")

    data = request.get_json(silent=True) or {}
    question       = (data.get("question") or "").strip()
    instructions   = (data.get("instructions") or "").strip()
    memory         = data.get("memory") or []
    history        = data.get("history") or []
    web_search_on  = bool(data.get("web_search_enabled"))
    model_choice   = MODEL_MAP.get(data.get("model"), MODEL_MAP["beni2"])
    image          = data.get("image")

    system = DAISY_SYSTEM
    if instructions:
        system += f"\n\nThe person has set these personal instructions for how you should behave: {instructions}"
    if memory:
        system += "\n\nThings you remember about this person: " + "; ".join(memory[-20:])

    messages = []
    for h in history[-24:]:
        role = "assistant" if h.get("role") in ("assistant", "daisy") else "user"
        messages.append({"role": role, "content": h.get("content", "")})

    user_content = []
    if image and image.get("data"):
        user_content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": image.get("media_type", "image/jpeg"), "data": image["data"]},
        })
    user_content.append({"type": "text", "text": question})
    messages.append({"role": "user", "content": user_content})

    try:
        import anthropic
    except ImportError:
        def _err():
            yield sse_line({"event": "final", "answer": "The `anthropic` package isn't installed on the server (pip install anthropic)."})
        return Response(_err(), mimetype="application/x-ndjson")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    def generate():
        yield sse_line({"event": "status", "status": "thinking"})
        will_build = looks_like_site_request(question)
        if will_build:
            yield sse_line({"event": "status", "status": "building"})
        elif web_search_on:
            yield sse_line({"event": "status", "status": "searching"})

        tools = [{"type": "web_search_20250305", "name": "web_search"}] if (web_search_on and not will_build) else None

        answer_text = ""
        used_web_search = False
        try:
            kwargs = dict(model=model_choice, max_tokens=4096, system=system, messages=messages)
            if tools:
                kwargs["tools"] = tools
            with client.messages.stream(**kwargs) as stream:
                for event in stream:
                    if event.type == "content_block_delta" and getattr(event.delta, "text", None):
                        answer_text += event.delta.text
                    if event.type == "content_block_start" and getattr(event.content_block, "type", "") == "server_tool_use":
                        used_web_search = True
                        yield sse_line({"event": "status", "status": "searching"})
            final = stream.get_final_message()
            # In case streaming text deltas missed anything (e.g. tool-use turns), fall back to the assembled message.
            if not answer_text:
                answer_text = "".join(b.text for b in final.content if getattr(b, "type", "") == "text")
        except Exception as e:
            yield sse_line({"event": "final", "answer": f"I couldn't reach my brain just now ({e}). Try again in a moment."})
            return

        bump_stats(len(answer_text.split()), f"/ask answered ({len(answer_text)} chars, model={model_choice})")
        yield sse_line({
            "event": "final",
            "answer": answer_text,
            "sources": [],
            "used_web_search": used_web_search,
            "memory_fact": None,
        })

    return Response(stream_with_context(generate()), mimetype="application/x-ndjson")

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


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
