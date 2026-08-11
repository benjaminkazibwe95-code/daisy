"""
TrustedBiz — app.py
All routes. Add your keys in environment variables and run.

on ENV VARS (add on Render dashboard):
  SECRET_KEY          = any random string
  ANTHROPIC_API_KEY   = sk-ant-... (from console.anthropic.com)
  DATABASE_URL        = auto-set by Render PostgreSQL
  CLOUDINARY_URL      = from cloudinary.com
  ADMIN_PASSWORD      = your secret admin password
  ADMIN_WHATSAPP      = 256753187966
  DGATEWAY_API_KEY    = (add when ready)
  DGATEWAY_MERCHANT_ID= (add when ready)
"""

import os, math, json, re, secrets, requests
from datetime import timedelta, datetime
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path
from flask import (Flask, render_template, request, redirect,
                   flash, session, jsonify, url_for)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# ── EMAIL ─────────────────────────────────────────────────────────────────────
import threading, urllib.request

_BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "")
_MAIL_FROM     = os.environ.get("MAIL_FROM", "TrustedBiz <hello@trustedbiz.co.ug>")

def _send_email(to, subject, html):
    """Send email via Brevo HTTP API in background thread — never blocks a request."""
    if not _BREVO_API_KEY:
        print(f"[EMAIL] BREVO_API_KEY not set — skipping email to {to}")
        return
    def _run():
        try:
            if "<" in _MAIL_FROM:
                fname, femail = _MAIL_FROM.split("<")
                fname  = fname.strip()
                femail = femail.strip("> ")
            else:
                fname  = "TrustedBiz"
                femail = _MAIL_FROM.strip()

            payload = json.dumps({
                "sender":  {"name": fname, "email": femail},
                "to":      [{"email": to}],
                "subject": subject,
                "htmlContent": html
            }).encode("utf-8")

            req = urllib.request.Request(
                "https://api.brevo.com/v3/smtp/email",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "api-key":      _BREVO_API_KEY,
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                print(f"[EMAIL] Sent '{subject}' → {to} (status {resp.status})")
        except Exception as e:
            print(f"[EMAIL] Failed to send to {to}: {e}")
    threading.Thread(target=_run, daemon=True).start()

def _email_welcome(name, email):
    _send_email(email, "Welcome to TrustedBiz! 🎉", f"""
<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:30px;">
<div style="max-width:560px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;">
  <div style="background:#2b7a78;padding:32px;text-align:center;">
    <h1 style="color:white;margin:0;font-size:28px;">Welcome to TrustedBiz!</h1>
    <p style="color:rgba(255,255,255,.8);margin:8px 0 0;">Uganda's Trusted Business Directory</p>
  </div>
  <div style="padding:32px;">
    <p style="font-size:16px;color:#333;">Hi <strong>{name}</strong>,</p>
    <p style="color:#555;line-height:1.7;">Your account is ready. You can now list your business and get a free AI-generated website that customers can find on Google.</p>
    <div style="text-align:center;margin:28px 0;">
      <a href="https://trustedbiz.co.ug/dashboard" style="background:#2b7a78;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;">Talk to Daisy →</a>
    </div>
    <p style="color:#888;font-size:13px;">If you didn't create this account, ignore this email.</p>
  </div>
  <div style="background:#f8f8f8;padding:16px;text-align:center;font-size:12px;color:#aaa;">
    © 2026 TrustedBiz · <a href="https://trustedbiz.co.ug" style="color:#2b7a78;">trustedbiz.co.ug</a>
  </div>
</div>
</body></html>""")

def ping_google(slug):
    """Ping Google to crawl and index a business URL immediately after approval."""
    import threading, urllib.request, urllib.parse
    def _ping(slug):
        try:
            biz_url   = f"https://{slug}.trustedbiz.co.ug"
            sitemap   = "https://trustedbiz.co.ug/sitemap.xml"
            # 1. Ping sitemap so Google re-reads all URLs
            ping_url  = f"https://www.google.com/ping?sitemap={urllib.parse.quote(sitemap, safe='')}"
            urllib.request.urlopen(ping_url, timeout=8)
            # 2. Also ping the specific business URL via IndexNow (Bing/Yandex — helps speed)
            indexnow  = (f"https://api.indexnow.org/indexnow"
                         f"?url={urllib.parse.quote(biz_url, safe='')}"
                         f"&key=trustedbiz2026")
            urllib.request.urlopen(indexnow, timeout=8)
            print(f"[SEO] Pinged Google + IndexNow for {biz_url}")
        except Exception as e:
            print(f"[SEO] Ping error for {slug}: {e}")
    threading.Thread(target=_ping, args=(slug,), daemon=True).start()

def _email_approved(name, email, biz_name, biz_slug):
    biz_url = f"https://{biz_slug}.trustedbiz.co.ug"
    _send_email(email, f"✅ {biz_name} is now LIVE on TrustedBiz!", f"""
<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:30px;">
<div style="max-width:560px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;">
  <div style="background:#22c55e;padding:32px;text-align:center;">
    <h1 style="color:white;margin:0;font-size:26px;">Your Business is Live! 🎉</h1>
  </div>
  <div style="padding:32px;">
    <p style="font-size:16px;color:#333;">Hi <strong>{name}</strong>,</p>
    <p style="color:#555;line-height:1.7;"><strong>{biz_name}</strong> has been approved and is now live on TrustedBiz. Customers in Uganda can find you right now.</p>
    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin:20px 0;">
      <p style="margin:0;color:#166534;font-size:14px;">🌐 Your business page:</p>
      <a href="{biz_url}" style="color:#2b7a78;font-weight:700;word-break:break-all;">{biz_url}</a>
    </div>
    <div style="text-align:center;margin:24px 0;">
      <a href="{biz_url}" style="background:#2b7a78;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;">View Your Page →</a>
    </div>
    <p style="color:#555;font-size:14px;">Share this link on WhatsApp, Facebook, and anywhere your customers are!</p>
  </div>
  <div style="background:#f8f8f8;padding:16px;text-align:center;font-size:12px;color:#aaa;">
    © 2026 TrustedBiz · <a href="https://trustedbiz.co.ug" style="color:#2b7a78;">trustedbiz.co.ug</a>
  </div>
</div>
</body></html>""")

def _email_submitted(name, email, biz_name):
    _send_email(email, f"📋 {biz_name} submitted — we're reviewing it", f"""
<!DOCTYPE html><html><body style="font-family:Arial,sans-serif;background:#f4f4f4;padding:30px;">
<div style="max-width:560px;margin:0 auto;background:white;border-radius:12px;overflow:hidden;">
  <div style="background:#2b7a78;padding:32px;text-align:center;">
    <h1 style="color:white;margin:0;font-size:24px;">Business Received! 📋</h1>
  </div>
  <div style="padding:32px;">
    <p style="font-size:16px;color:#333;">Hi <strong>{name}</strong>,</p>
    <p style="color:#555;line-height:1.7;">We've received your listing for <strong>{biz_name}</strong>. Our team will review and approve it within 24 hours. You'll get another email the moment it goes live.</p>
    <div style="text-align:center;margin:24px 0;">
      <table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center"><a href="https://trustedbiz.co.ug/dashboard" style="display:inline-block;background:#2b7a78;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;font-size:15px;mso-padding-alt:0;">View Dashboard →</a></td></tr></table>
    </div>
  </div>
  <div style="background:#f8f8f8;padding:16px;text-align:center;font-size:12px;color:#aaa;">
    © 2026 TrustedBiz · <a href="https://trustedbiz.co.ug" style="color:#2b7a78;">trustedbiz.co.ug</a>
  </div>
</div>
</body></html>""")

# ── APP ───────────────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = True
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100 MB

ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','webp'}

# ── SUBDOMAIN MIDDLEWARE ───────────────────────────────────────────────────────
@app.before_request
def handle_subdomain():
    host = request.host.lower().split(':')[0]  # e.g. cyber-tech.trustedbiz.co.ug
    if host.endswith('.trustedbiz.co.ug'):
        slug = host.replace('.trustedbiz.co.ug', '')
        if slug and slug not in ('www', 'admin', 'api'):
            if request.path == '/':
                # Serve the business page without changing the URL
                return site(slug)
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD", "trustedbiz2026")
ADMIN_WHATSAPP  = os.environ.get("ADMIN_WHATSAPP", "256753187966")

def allowed_file(f):
    return '.' in f and f.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS

def similar(a,b):
    return SequenceMatcher(None,a,b).ratio()

# ── DATABASE ──────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL","")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://","postgresql://",1)
USE_POSTGRES = bool(DATABASE_URL)

if USE_POSTGRES:
    import psycopg2, psycopg2.extras
    def get_db():
        return psycopg2.connect(DATABASE_URL,
               cursor_factory=psycopg2.extras.RealDictCursor)
else:
    import sqlite3
    DB_PATH = os.environ.get("DB_PATH","database.db")
    def get_db():
        conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

def q(sql):
    return sql.replace("?","%s") if USE_POSTGRES else sql

# ── IMAGE STORAGE ─────────────────────────────────────────────────────────────
CLOUDINARY_URL = os.environ.get("CLOUDINARY_URL","")
USE_CLOUDINARY = bool(CLOUDINARY_URL)
if USE_CLOUDINARY:
    import cloudinary, cloudinary.uploader
    cloudinary.config(cloudinary_url=CLOUDINARY_URL)

LOCAL_UPLOAD = Path("static/images")
LOCAL_UPLOAD.mkdir(parents=True, exist_ok=True)

def save_photos(files):
    results = []
    for photo in files:
        if not photo or not photo.filename: continue
        if not allowed_file(photo.filename): continue
        try:
            if USE_CLOUDINARY:
                up = cloudinary.uploader.upload(photo, folder="trustedbiz",
                     transformation=[{"width":1200,"height":900,"crop":"limit","quality":"auto:good"}])
                results.append(up["secure_url"])
            else:
                ext = photo.filename.rsplit('.',1)[1].lower()
                fname = f"{secrets.token_hex(8)}.{ext}"
                photo.save(str(LOCAL_UPLOAD/fname))
                results.append(fname)
        except Exception as e:
            print(f"Photo error: {e}")
    return results

def save_photos_b64(b64_list):
    """Save client-compressed base64 images — avoids 413 entity too large."""
    import base64, re
    results = []
    for b64 in b64_list:
        if not b64 or b64 == 'null': continue
        try:
            match = re.match(r'data:image/(\w+);base64,(.+)', b64, re.DOTALL)
            if not match: continue
            ext, data = match.group(1), match.group(2)
            if ext not in ('jpeg','jpg','png','webp'): ext = 'jpg'
            raw = base64.b64decode(data)
            if not raw or len(raw) < 100: continue
            if USE_CLOUDINARY:
                up = cloudinary.uploader.upload(raw, folder="trustedbiz",
                     transformation=[{"width":1200,"height":900,"crop":"limit","quality":"auto:good"}])
                results.append(up["secure_url"])
            else:
                fname = f"{secrets.token_hex(8)}.{ext}"
                (LOCAL_UPLOAD/fname).write_bytes(raw)
                results.append(fname)
        except Exception as e:
            print(f"B64 photo error: {e}")
    return results

def save_single_photo(file):
    results = save_photos([file])
    return results[0] if results else None

def photo_url(ref):
    if not ref: return ""
    if ref.startswith("http"): return ref
    return f"/static/images/{ref}"

app.jinja_env.globals['photo_url'] = photo_url

# ── DB HELPERS ────────────────────────────────────────────────────────────────
def db_fetchall(sql, params=()):
    conn = get_db()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(sql,params); return cur.fetchall()
        return conn.execute(sql,params).fetchall()
    finally: conn.close()

def db_fetchone(sql, params=()):
    conn = get_db()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(sql,params); return cur.fetchone()
        return conn.execute(sql,params).fetchone()
    finally: conn.close()

def db_execute(sql, params=()):
    conn = get_db()
    try:
        if USE_POSTGRES:
            cur = conn.cursor(); cur.execute(sql,params); conn.commit()
        else:
            conn.execute(sql,params); conn.commit()
    except Exception as e:
        try: conn.rollback()
        except: pass
        print(f"db_execute error: {e}"); raise
    finally: conn.close()

def db_insert(sql, params=()):
    conn = get_db()
    try:
        if USE_POSTGRES:
            if "RETURNING" not in sql.upper():
                sql = sql.rstrip(';') + " RETURNING id"
            cur = conn.cursor(); cur.execute(sql,params)
            row = cur.fetchone(); conn.commit()
            return row['id'] if row else None
        else:
            cur = conn.execute(sql,params); conn.commit(); return cur.lastrowid
    except Exception as e:
        try: conn.rollback()
        except: pass
        raise
    finally: conn.close()

# ── TABLES ────────────────────────────────────────────────────────────────────
def create_tables():
    conn = get_db()
    tables = []
    if USE_POSTGRES:
        tables = [
        "CREATE TABLE IF NOT EXISTS users (id SERIAL PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user', is_premium INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS business (id SERIAL PRIMARY KEY, name TEXT, category TEXT, whatsapp TEXT, lat REAL, lng REAL, photos TEXT, description TEXT, hours TEXT, status TEXT DEFAULT 'approved', verified INTEGER DEFAULT 0, reports INTEGER DEFAULT 0, views INTEGER DEFAULT 0, owner_id INTEGER, owner_ip TEXT, is_premium INTEGER DEFAULT 0, plan TEXT DEFAULT 'free', brand_color TEXT DEFAULT '#2b7a78', slug TEXT UNIQUE, hero_price REAL, hero_price_label TEXT, generated_html TEXT, last_payment_date DATE, free_trial_end DATE, payment_months_late INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS branches (id SERIAL PRIMARY KEY, business_id INTEGER, name TEXT, address TEXT, whatsapp TEXT, hours TEXT, lat REAL, lng REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS reviews (id SERIAL PRIMARY KEY, business_id INTEGER, user_id INTEGER, rating INTEGER, comment TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS reports (id SERIAL PRIMARY KEY, business_id INTEGER, user_identifier TEXT)",
        "CREATE TABLE IF NOT EXISTS notifications (id SERIAL PRIMARY KEY, user_id INTEGER, user_identifier TEXT, message TEXT, seen INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS price_guard_items (id SERIAL PRIMARY KEY, business_id INTEGER, category TEXT, label TEXT, price REAL, image_ref TEXT, ai_name TEXT, ai_verified INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS ads (id SERIAL PRIMARY KEY, business_id INTEGER, title TEXT, body TEXT, image_ref TEXT, active INTEGER DEFAULT 1, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS agents (id SERIAL PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, whatsapp TEXT, area TEXT, code TEXT UNIQUE, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS template_pool (id SERIAL PRIMARY KEY, category TEXT NOT NULL, html TEXT NOT NULL, quality_score INTEGER DEFAULT 0, times_used INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS invite_codes (id SERIAL PRIMARY KEY, code TEXT UNIQUE NOT NULL, biz_id INTEGER, agent_id INTEGER, plan TEXT DEFAULT 'promax', used INTEGER DEFAULT 0, used_by_user_id INTEGER, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS daisy_training (id SERIAL PRIMARY KEY, input TEXT NOT NULL, output TEXT NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        ]
        cur = conn.cursor()
        for t in tables: cur.execute(t)
        conn.commit(); cur.close()
    else:
        tables = [
        "CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user', is_premium INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS business (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, category TEXT, whatsapp TEXT, lat REAL, lng REAL, photos TEXT, description TEXT, hours TEXT, status TEXT DEFAULT 'approved', verified INTEGER DEFAULT 0, reports INTEGER DEFAULT 0, views INTEGER DEFAULT 0, owner_id INTEGER, owner_ip TEXT, is_premium INTEGER DEFAULT 0, brand_color TEXT DEFAULT '#2b7a78', slug TEXT UNIQUE, hero_price REAL, hero_price_label TEXT, generated_html TEXT, last_payment_date DATE, payment_months_late INTEGER DEFAULT 0, plan TEXT DEFAULT 'free', location TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS branches (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER, name TEXT, address TEXT, whatsapp TEXT, hours TEXT, lat REAL, lng REAL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS reviews (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER, user_id INTEGER, rating INTEGER, comment TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS reports (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER, user_identifier TEXT)",
        "CREATE TABLE IF NOT EXISTS notifications (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, user_identifier TEXT, message TEXT, seen INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS price_guard_items (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER, category TEXT, label TEXT, price REAL, image_ref TEXT, ai_name TEXT, ai_verified INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS ads (id INTEGER PRIMARY KEY AUTOINCREMENT, business_id INTEGER, title TEXT, body TEXT, image_ref TEXT, active INTEGER DEFAULT 1, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS agents (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password TEXT, whatsapp TEXT, area TEXT, code TEXT UNIQUE, status TEXT DEFAULT 'active', created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS template_pool (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT NOT NULL, html TEXT NOT NULL, quality_score INTEGER DEFAULT 0, times_used INTEGER DEFAULT 0, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS invite_codes (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE NOT NULL, biz_id INTEGER, agent_id INTEGER, plan TEXT DEFAULT 'promax', used INTEGER DEFAULT 0, used_by_user_id INTEGER, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        "CREATE TABLE IF NOT EXISTS daisy_training (id INTEGER PRIMARY KEY AUTOINCREMENT, input TEXT NOT NULL, output TEXT NOT NULL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP)",
        ]
        for t in tables: conn.execute(t)
        conn.commit()
    conn.close()

try: create_tables()
except Exception as e: print(f"DB init: {e}")

# ── HELPERS ───────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(f'/login?next={request.path}')
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('admin_auth'):
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    if 'user_id' in session:
        return db_fetchone(q("SELECT * FROM users WHERE id=?"), (session['user_id'],))
    return None

def make_slug(name):
    slug = re.sub(r'[^a-z0-9]+','-',name.lower()).strip('-')[:60]
    base, i = slug, 1
    while db_fetchone(q("SELECT id FROM business WHERE slug=?"), (slug,)):
        slug = f"{base}-{i}"; i += 1
    return slug

def haversine(lat1,lon1,lat2,lon2):
    R=6371; d=lambda x:math.radians(x)
    a=(math.sin(d(lat2-lat1)/2)**2 + math.cos(d(lat1))*math.cos(d(lat2))*math.sin(d(lon2-lon1)/2)**2)
    return R*2*math.atan2(math.sqrt(a),math.sqrt(1-a))

def biz_to_dict(b):
    from datetime import datetime, date
    d = dict(b)
    for k,v in d.items():
        if isinstance(v,(datetime,date)): d[k] = v.isoformat()
    return d

def get_anthropic_client():
    key = os.environ.get("ANTHROPIC_API_KEY","")
    if not key: return None
    try:
        import anthropic
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        return None

# ── DAISY API CLIENT ──────────────────────────────────────────────────────────
# Daisy is a separate deployment (her own Render service). TrustedBiz talks to
# her over HTTP instead of generating content locally. Set these two env vars
# once her API is live:
#   DAISY_API_URL = https://daisy-xxxx.onrender.com   (no trailing slash)
#   DAISY_API_KEY = shared secret so only TrustedBiz can call her
DAISY_API_URL = os.environ.get("DAISY_API_URL", "").rstrip("/")
DAISY_API_KEY = os.environ.get("DAISY_API_KEY", "")

def _daisy_extract_html(text):
    """Pull the HTML out of the ```html:filename.html fenced block Daisy is
    instructed to reply with. Falls back to a plain ```html fence too."""
    if not text:
        return None
    m = re.search(r"```html:[^\n]*\n(.*?)```", text, re.DOTALL)
    if not m:
        m = re.search(r"```html\n(.*?)```", text, re.DOTALL)
    return m.group(1).strip() if m else None

def call_daisy(mode, context=None, history=None, message=None, timeout=55):
    """
    Calls Daisy's real API: POST {DAISY_API_URL}/api/ask, Bearer auth,
    body {question, history, instructions, model}, response is a stream of
    newline-delimited JSON events ending in {"event":"final","answer":...}.

    Daisy is a general chat/knowledge assistant, not a purpose-built site
    generator or a business-info collector, so both of those behaviors are
    built here by shaping what we ask her and parsing what she sends back.
    Returns (result_dict, error_string) in the same shape every call site
    already expects:
      mode='website' -> {'html': <str>}
      mode='chat'     -> {'reply': <str>, 'mode': 'website' or None}
    Never raises — callers don't need try/except.
    """
    if not DAISY_API_URL:
        return None, "Daisy isn't connected yet — set DAISY_API_URL."
    if not DAISY_API_KEY:
        return None, "Daisy isn't connected yet — set DAISY_API_KEY."

    context = context or {}
    history = history or []

    # Translate our own {role, content} history shape into Daisy's
    # {user, daisy, topic} turn shape.
    daisy_history = []
    for h in history:
        role = h.get('role')
        content = h.get('content', '')
        if role == 'user':
            daisy_history.append({'user': content, 'daisy': '', 'topic': mode})
        elif role in ('assistant', 'daisy') and daisy_history:
            daisy_history[-1]['daisy'] = content

    if mode == 'website':
        name     = context.get('name') or 'this business'
        category = context.get('category') or 'a local Ugandan business'
        desc     = context.get('description') or context.get('desc') or ''
        question = (
            f'Build a complete, polished single-page business website for "{name}", '
            f'a {category} business in Uganda. What it does: {desc}. '
            f'Reply with ONLY one ```html:site.html fenced code block containing the '
            f'full, self-contained HTML with inline CSS/JS — no explanation, no chat '
            f'text before or after the fence.'
        )
        instructions = "You are generating a real, live website file for a customer. Output only the code fence, nothing else, no preamble."
    else:  # 'chat'
        question = message or ''
        instructions = (
            "You are Daisy, TrustedBiz's site-building assistant, helping someone "
            "describe a business they want online. Chat naturally and ask what's "
            "still missing. The moment you have the business's name, its category, "
            "and roughly what it does, end your reply on its own new line with "
            "exactly: [[READY]] — never explain that marker, just include it."
        )

    payload = {
        "question": question,
        "history": daisy_history,
        "instructions": instructions,
        "model": "beni2",
        "web_search_enabled": False,
    }

    resp = None
    try:
        resp = requests.post(
            f"{DAISY_API_URL}/api/ask",
            json=payload,
            headers={"Authorization": f"Bearer {DAISY_API_KEY}"},
            timeout=timeout,
        )
        resp.raise_for_status()

        answer = None
        for line in resp.text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if evt.get('event') == 'final':
                answer = evt.get('answer', '')
        if answer is None:
            try:
                single = resp.json()
                answer = single.get('answer') or single.get('reply') or ''
            except ValueError:
                answer = resp.text

        if mode == 'website':
            html = _daisy_extract_html(answer)
            if not html:
                return None, "Daisy replied but didn't send back a usable site file. Try again."
            return {'html': html}, None
        else:
            ready = '[[READY]]' in (answer or '')
            reply = (answer or '').replace('[[READY]]', '').strip()
            return {'reply': reply, 'mode': 'website' if ready else None}, None

    except requests.exceptions.Timeout:
        return None, "Daisy is thinking hard on this one. Please try again in a moment."
    except requests.exceptions.HTTPError as e:
        print(f"[Daisy API] {e}")
        if resp is not None and resp.status_code == 401:
            return None, "Daisy's key doesn't match — check DAISY_API_KEY on both sides."
        return None, "Daisy couldn't be reached right now. Please try again shortly."
    except Exception as e:
        print(f"[Daisy API] {e}")
        return None, "Daisy couldn't be reached right now. Please try again shortly."

# ── HOME ──────────────────────────────────────────────────────────────────────
@app.route('/')
def home():
    query    = request.args.get('query','').strip()
    user_lat = request.args.get('lat', type=float)
    user_lng = request.args.get('lng', type=float)

    all_biz = db_fetchall(
        q("SELECT * FROM business WHERE status='approved' ORDER BY is_premium DESC, id DESC"))

    if query:
        ql = query.lower()
        scored = []
        for b in all_biz:
            bd = biz_to_dict(b)
            s = max(similar(ql,(bd.get('name') or '').lower()),
                    similar(ql,(bd.get('category') or '').lower()))
            if ql in (bd.get('name') or '').lower() or \
               ql in (bd.get('category') or '').lower() or s > 0.45:
                scored.append((b,s))
        scored.sort(key=lambda x:x[1],reverse=True)
        filtered = [b for b,_ in scored]
    else:
        filtered = list(all_biz)

    results = []
    for b in filtered:
        bd = biz_to_dict(b)
        dist = 9999.0
        if user_lat and user_lng and bd.get('lat') and bd.get('lng'):
            try: dist = haversine(user_lat,user_lng,float(bd['lat']),float(bd['lng']))
            except: pass
        rv = db_fetchone(
            q("SELECT AVG(rating) as avg_rating, COUNT(*) as cnt FROM reviews WHERE business_id=?"),
            (bd['id'],))
        bd['avg_rating']   = round(float(rv['avg_rating']),1) if rv and rv['avg_rating'] else 0
        bd['review_count'] = rv['cnt'] if rv else 0
        results.append((bd, round(dist,2)))

    if user_lat and user_lng:
        results.sort(key=lambda x:(0 if x[0].get('is_premium') else 1, x[1]))

    notifications = []
    if 'user_id' in session:
        rows = db_fetchall(
            q("SELECT * FROM notifications WHERE user_id=? AND seen=0 ORDER BY created_at DESC LIMIT 5"),
            (session['user_id'],))
        notifications = [dict(r) for r in rows]
        if notifications:
            db_execute(q("UPDATE notifications SET seen=1 WHERE user_id=?"), (session['user_id'],))

    # ── WEB SEARCH ─────────────────────────────────────────────────────────────
    web_results = []
    if query:
        try:
            import urllib.request, urllib.parse, json as _json
            search_query = urllib.parse.quote(f"{query} Uganda business")
            url = f"https://api.duckduckgo.com/?q={search_query}&format=json&no_html=1&skip_disambig=1"
            req = urllib.request.Request(url, headers={'User-Agent': 'TrustedBiz/1.0'})
            with urllib.request.urlopen(req, timeout=5) as r:
                data = _json.loads(r.read().decode())
            for item in (data.get('RelatedTopics') or [])[:10]:
                if isinstance(item, dict) and item.get('Text') and item.get('FirstURL'):
                    icon = (item.get('Icon') or {}).get('URL', '')
                    domain = item['FirstURL'].split('/')[2] if '//' in item['FirstURL'] else ''
                    web_results.append({
                        'title': item['Text'].split(' - ')[0][:90],
                        'snippet': item['Text'][:220],
                        'url': item['FirstURL'],
                        'image': ('https://duckduckgo.com' + icon) if icon and icon.startswith('/') else icon,
                        'source': domain,
                    })
        except Exception as e:
            print(f"Web search error: {e}")

    return render_template('home.html',
        results=results,
        web_results=web_results,
        current_user=get_current_user(),
        notifications=notifications)

# ── PRICE GUARD API ───────────────────────────────────────────────────────────
@app.route('/portfolio')
def portfolio():
    # Folded into /about — redirect so any existing links/bookmarks still work
    return redirect('/about', code=301)


@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        name  = request.form.get('name','').strip()[:100]
        email = request.form.get('email','').lower().strip()
        pwd   = request.form.get('password','')
        conf  = request.form.get('confirm','')
        if not name or not email: flash("All fields are required."); return render_template('register.html',current_user=None)
        if len(pwd) < 6: flash("Password must be at least 6 characters."); return render_template('register.html',current_user=None)
        if pwd != conf: flash("Passwords do not match."); return render_template('register.html',current_user=None)
        try:
            user_id = db_insert(q("INSERT INTO users (name,email,password) VALUES (?,?,?)"),
                      (name,email,generate_password_hash(pwd)))
            session.permanent = True
            session['user_id'] = user_id
            session['user_name'] = name
            _email_welcome(name, email)
            flash(f"Welcome {name}! Tell Daisy about your business to get your first site live.")
            return redirect('/dashboard')
        except: flash("Email already registered.")
    return render_template('register.html', current_user=None)

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').lower().strip()
        pwd   = request.form.get('password','')
        user  = db_fetchone(q("SELECT * FROM users WHERE email=?"), (email,))
        if user and check_password_hash(user['password'], pwd):
            session.permanent = True
            session['user_id']   = user['id']
            session['user_name'] = user['name']
            return redirect(request.args.get('next','/dashboard'))
        flash("Wrong email or password.")
    return render_template('login.html', current_user=None)

@app.route('/logout')
def logout():
    session.clear(); return redirect('/')

# ── AI WEBSITE VIEW ───────────────────────────────────────────────────────────
@app.route('/site/<slug>')
def site(slug=None):
    # Also handle subdomain requests e.g. cyber-tech.trustedbiz.co.ug
    if slug is None:
        from flask import g
        slug = getattr(g, 'subdomain_slug', None)
        if not slug:
            return render_template('404.html', current_user=get_current_user()), 404
    biz = db_fetchone(q("SELECT * FROM business WHERE slug=? AND status='approved'"), (slug,))
    if not biz:
        try: biz = db_fetchone(q("SELECT * FROM business WHERE id=? AND status='approved'"), (int(slug),))
        except: pass
    if not biz: return render_template('404.html', current_user=get_current_user()), 404

    db_execute(q("UPDATE business SET views=views+1 WHERE id=?"), (biz['id'],))
    bd = biz_to_dict(biz)

    # Get ads for this business
    ads = db_fetchall(q("SELECT * FROM ads WHERE business_id=? AND active=1 ORDER BY updated_at DESC LIMIT 2"), (biz['id'],))
    bd['ads'] = [dict(a) for a in ads]

    # Get branches
    branches = db_fetchall(q("SELECT * FROM branches WHERE business_id=? ORDER BY id"), (biz['id'],))
    bd['branches'] = [dict(b) for b in branches]

    # Get reviews
    reviews = db_fetchall(q("""
        SELECT r.*, u.name as reviewer_name FROM reviews r
        LEFT JOIN users u ON u.id=r.user_id
        WHERE r.business_id=? ORDER BY r.created_at DESC
    """), (biz['id'],))
    rv_avg = db_fetchone(q("SELECT AVG(rating) as a, COUNT(*) as c FROM reviews WHERE business_id=?"), (biz['id'],))
    bd['avg_rating']    = round(float(rv_avg['a']),1) if rv_avg and rv_avg['a'] else 0
    bd['total_reviews'] = rv_avg['c'] if rv_avg else 0

    if bd.get('generated_html'):
        return bd['generated_html']

    # Generate the site via Daisy's API instead of generating locally.
    # A simple, real-data fallback (using the business's actual name, category,
    # description, photos, hours, branches) is saved and served immediately so
    # the visitor never sees a blank page. Daisy's real build then runs and
    # replaces it — currently synchronous (page waits up to ~55s on first
    # visit); move this to a background thread once Daisy's API is live and
    # you know her real response time.
    wa_link = f"https://wa.me/{bd.get('whatsapp','')}"
    fallback_html = _basic_fallback_site(bd, wa_link)

    daisy_ctx = {
        'name': bd.get('name'), 'category': bd.get('category'),
        'description': bd.get('description'), 'whatsapp': bd.get('whatsapp'),
        'hours': bd.get('hours') or 'Mon-Sat 8am-7pm',
        'brand_color': bd.get('brand_color') or '#2b7a78',
        'photos': [p.strip() for p in str(bd.get('photos') or '').split(',') if p.strip()],
        'branches': bd.get('branches') or [], 'ads': bd.get('ads') or [],
    }
    result, err = call_daisy('website', context=daisy_ctx)
    html = (result or {}).get('html') if result else None

    if html:
        try: db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (html, biz['id']))
        except: pass
        return html

    # Daisy unavailable or still not connected — serve the fallback, don't
    # leave the visitor with nothing, but don't cache it as the final site.
    print(f"[Daisy API] site() fallback used for biz {biz['id']}: {err}")
    return fallback_html


def _basic_fallback_site(bd, wa_link):
    """Plain, real-data page shown only if Daisy's API is unreachable."""
    name  = str(bd.get('name') or 'Business')
    cat   = str(bd.get('category') or '')
    desc  = str(bd.get('description') or '')
    hours = str(bd.get('hours') or 'Mon-Sat 8am-7pm')
    color = str(bd.get('brand_color') or '#2b7a78')
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} | TrustedBiz</title>
<style>body{{font-family:sans-serif;background:#f5f8f8;color:#0d1c1c;max-width:560px;margin:60px auto;padding:0 24px;text-align:center;}}
h1{{color:{color};}} a{{display:inline-block;margin-top:20px;background:{color};color:#fff;padding:12px 26px;border-radius:6px;text-decoration:none;font-weight:600;}}</style>
</head><body><h1>{name}</h1><p>{cat}</p><p>{desc}</p><p>{hours}</p>
<a href="{wa_link}">Message on WhatsApp</a></body></html>"""

# ── REGENERATE ────────────────────────────────────────────────────────────────
@app.route('/daisy/create-business', methods=['POST'])
@login_required
def daisy_create_business():
    """The real gap this closes: Daisy's chat can describe a business and
    even preview its HTML, but nothing saved it. This turns that into an
    actual live listing — called once the conversation has a name and
    enough detail to go live."""
    user = get_current_user()
    data = request.get_json() or {}
    name        = (data.get('name') or '').strip()[:100]
    category    = (data.get('category') or '').strip().lower()
    whatsapp    = (data.get('whatsapp') or '').strip()
    description = (data.get('description') or '').strip()
    color       = (data.get('brand_color') or '#2b7a78').strip()
    html        = data.get('html')  # Daisy may have already generated this during chat

    if not name:
        return jsonify({'error': 'A business name is required.'}), 400

    slug = make_slug(name)
    biz_id = db_insert(
        q("INSERT INTO business (name, category, whatsapp, description, brand_color, slug, owner_id, status, plan, generated_html) VALUES (?,?,?,?,?,?,?,?,?,?)"),
        (name, category, whatsapp, description, color, slug, user['id'], 'approved', 'free', html)
    )
    ping_google(slug)

    if not html:
        daisy_ctx = {'name': name, 'category': category, 'description': description,
                     'whatsapp': whatsapp, 'brand_color': color, 'hours': 'Mon-Sat 8am-7pm'}
        def _bg(ctx, bid):
            result, err = call_daisy('website', context=ctx)
            h = (result or {}).get('html') if result else None
            if h:
                try: db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (h, bid))
                except Exception as e: print(f"create-business save error: {e}")
            else:
                print(f"[Daisy API] create-business gen failed for biz {bid}: {err}")
        import threading
        threading.Thread(target=_bg, args=(daisy_ctx, biz_id), daemon=True).start()

    return jsonify({'success': True, 'biz_id': biz_id, 'slug': slug,
                     'url': f"https://{slug}.trustedbiz.co.ug"})


@app.route('/generate-site/<int:biz_id>', methods=['POST'])
@login_required
def generate_site(biz_id):
    biz = db_fetchone(q("SELECT * FROM business WHERE id=? AND owner_id=?"), (biz_id,session['user_id']))
    if not biz: flash("Not found."); return redirect('/dashboard#sites')
    bd = biz_to_dict(biz)
    branches = db_fetchall(q("SELECT * FROM branches WHERE business_id=?"), (biz_id,))
    bd['branches'] = [dict(b) for b in branches]
    ads = db_fetchall(q("SELECT * FROM ads WHERE business_id=? AND active=1 LIMIT 2"), (biz_id,))
    bd['ads'] = [dict(a) for a in ads]
    daisy_ctx = {
        'name': bd.get('name'), 'category': bd.get('category'),
        'description': bd.get('description'), 'whatsapp': bd.get('whatsapp'),
        'hours': bd.get('hours') or 'Mon-Sat 8am-7pm',
        'brand_color': bd.get('brand_color') or '#2b7a78',
        'photos': [p.strip() for p in str(bd.get('photos') or '').split(',') if p.strip()],
        'branches': bd.get('branches') or [], 'ads': bd.get('ads') or [],
        'plan': bd.get('plan') or 'free',
    }

    def _regen_bg(ctx, biz_id):
        result, err = call_daisy('website', context=ctx)
        html = (result or {}).get('html') if result else None
        if html:
            try: db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (html, biz_id))
            except Exception as e: print(f"Regen save error: {e}")
        else:
            print(f"[Daisy API] regen failed for biz {biz_id}: {err}")

    import threading
    threading.Thread(target=_regen_bg, args=(daisy_ctx, biz_id), daemon=True).start()
    flash("✨ Your website is being rebuilt by Daisy... refresh in about 30 seconds to see it live.")
    return redirect('/dashboard')

# ── ADD BUSINESS ──────────────────────────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    user_id    = session['user_id']
    businesses = db_fetchall(q("SELECT * FROM business WHERE owner_id=? ORDER BY created_at DESC"), (user_id,))
    stats = {}
    for b in businesses:
        rv = db_fetchone(q("SELECT AVG(rating) as a, COUNT(*) as c FROM reviews WHERE business_id=?"), (b['id'],))
        stats[b['id']] = {"avg_rating": round(float(rv['a']),1) if rv and rv['a'] else 0, "total_reviews": rv['c'] if rv else 0}
    businesses   = [biz_to_dict(b) for b in businesses]
    total_views  = sum(b.get('views',0) or 0 for b in businesses)
    live_count   = sum(1 for b in businesses if b.get('status')=='approved')
    # Plan is tracked per-business (see admin's Set Basic/Set Pro Max). For the
    # account-level billing view, use the highest plan among the user's
    # businesses as "chosen_plan" — reasonable for the common single-business
    # case; worth revisiting once multi-business accounts are common.
    plan_rank = {'free': 0, 'basic': 1, 'pro_max': 2}
    chosen_plan = 'free'
    for b in businesses:
        if plan_rank.get(b.get('plan') or 'free', 0) > plan_rank.get(chosen_plan, 0):
            chosen_plan = b.get('plan') or 'free'
    current_user = get_current_user()
    return render_template('console.html', businesses=businesses, stats=stats,
                           current_user=current_user, total_listings=len(businesses),
                           live_count=live_count, total_views=total_views,
                           chosen_plan=chosen_plan)

# ── DASHBOARD SET COLOR ───────────────────────────────────────────────────────
@app.route('/dashboard/set-template/<int:biz_id>', methods=['POST'])
@login_required
def set_template(biz_id):
    color = request.form.get('brand_color','#2b7a78')
    biz   = db_fetchone(q("SELECT id FROM business WHERE id=? AND owner_id=?"), (biz_id,session['user_id']))
    if biz:
        db_execute(q("UPDATE business SET brand_color=?,generated_html=NULL WHERE id=?"), (color,biz_id))
        flash("Color saved! Regenerating website…")
    return redirect('/dashboard')

# ── REVIEW ────────────────────────────────────────────────────────────────────
@app.route('/review/<int:biz_id>', methods=['POST'])
@login_required
def submit_review(biz_id):
    rating  = request.form.get('rating')
    comment = request.form.get('comment','').strip()
    user_id = session['user_id']
    if not rating: flash("Please select a star rating."); return redirect(f'/site/{biz_id}')
    existing = db_fetchone(q("SELECT id FROM reviews WHERE business_id=? AND user_id=?"), (biz_id,user_id))
    if existing: flash("You already reviewed this business."); return redirect(f'/site/{biz_id}')
    db_insert(q("INSERT INTO reviews (business_id,user_id,rating,comment) VALUES (?,?,?,?)"),
              (biz_id,user_id,rating,comment))
    flash("Review submitted! Thank you.")
    return redirect(f'/site/{biz_id}')

# ── REPORT ────────────────────────────────────────────────────────────────────
@app.route('/report/<int:biz_id>')
def report(biz_id):
    ip = request.remote_addr
    if db_fetchone(q("SELECT id FROM reports WHERE business_id=? AND user_identifier=?"), (biz_id,ip)):
        flash("You already reported this business."); return redirect('/')
    db_execute(q("INSERT INTO reports (business_id,user_identifier) VALUES (?,?)"), (biz_id,ip))
    db_execute(q("UPDATE business SET reports=reports+1 WHERE id=?"), (biz_id,))
    flash("Business reported. Thank you."); return redirect('/')

# ── UPGRADE ───────────────────────────────────────────────────────────────────
# The dedicated upgrade page was cut — the console's Billing tab already has
# the same WhatsApp upgrade links built in. Redirect so old links still work.
@app.route('/upgrade/<int:biz_id>')
@login_required
def upgrade_page(biz_id):
    return redirect('/dashboard')

# ── ADMIN LOGIN ───────────────────────────────────────────────────────────────
@app.route('/admin/login', methods=['GET','POST'])
def admin_login():
    if request.method == 'POST':
        if request.form.get('admin_pass') == ADMIN_PASSWORD:
            session['admin'] = True; return redirect('/admin')
        flash("Wrong password.")
    return render_template('admin_login.html', current_user=None)

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_auth',None); return redirect('/')

# ── ADMIN PANEL ───────────────────────────────────────────────────────────────
@app.route('/admin', methods=['GET','POST'])
def admin():
    if not session.get('admin_auth'):
        if request.method == 'POST' and request.form.get('admin_pass') == ADMIN_PASSWORD:
            session.permanent = True
            session['admin_auth'] = True
        else:
            if request.method == 'POST':
                flash("Wrong password.")
            return render_template('admin_login.html', current_user=None)

    if request.method == 'POST':
        biz_id = request.form.get('id')
        action = request.form.get('action')

        if action == 'approve':
            db_execute(q("UPDATE business SET status='approved' WHERE id=?"), (biz_id,))
            owner = db_fetchone(q("SELECT owner_id FROM business WHERE id=?"), (biz_id,))
            biz_row = db_fetchone(q("SELECT name, slug FROM business WHERE id=?"), (biz_id,))
            # Ping Google to index the business URL immediately
            if biz_row and biz_row.get('slug'):
                ping_google(biz_row['slug'])
            if owner and owner.get('owner_id'):
                db_insert(q("INSERT INTO notifications (user_id,message) VALUES (?,?)"),
                          (owner['owner_id'], "✅ Your business is now live on TrustedBiz!"))
                # Send approval email
                user_row = db_fetchone(q("SELECT name, email FROM users WHERE id=?"), (owner['owner_id'],))
                if biz_row and user_row:
                    _email_approved(user_row['name'], user_row['email'], biz_row['name'], biz_row['slug'])
        elif action == 'reject':
            db_execute(q("UPDATE business SET status='rejected' WHERE id=?"), (biz_id,))
        elif action == 'verify':
            db_execute(q("UPDATE business SET verified=1 WHERE id=?"), (biz_id,))
        elif action == 'unverify':
            db_execute(q("UPDATE business SET verified=0 WHERE id=?"), (biz_id,))
        elif action in ('set_basic', 'set_pro_max', 'set_free'):
            new_plan = {'set_basic': 'basic', 'set_pro_max': 'pro_max', 'set_free': 'free'}[action]
            is_paid  = 1 if new_plan != 'free' else 0
            db_execute(q("UPDATE business SET plan=?, is_premium=?, last_payment_date=CURRENT_DATE, payment_months_late=0 WHERE id=?"),
                       (new_plan, is_paid, biz_id))
            owner = db_fetchone(q("SELECT owner_id FROM business WHERE id=?"), (biz_id,))
            if owner and owner.get('owner_id'):
                label = {'basic': 'Basic', 'pro_max': 'Pro Max', 'free': 'Free'}[new_plan]
                db_insert(q("INSERT INTO notifications (user_id,message) VALUES (?,?)"),
                          (owner['owner_id'], f"Your plan is now {label}."))
        elif action == 'mark_late':
            db_execute(q("UPDATE business SET payment_months_late=payment_months_late+1 WHERE id=?"), (biz_id,))
        elif action == 'block':
            db_execute(q("UPDATE business SET status='rejected' WHERE id=?"), (biz_id,))
        elif action == 'regen':
            biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (biz_id,))
            if biz:
                bd = biz_to_dict(biz)
                branches = db_fetchall(q("SELECT * FROM branches WHERE business_id=?"), (biz_id,))
                bd['branches'] = [dict(b) for b in branches]
                ads = db_fetchall(q("SELECT * FROM ads WHERE business_id=? AND active=1 LIMIT 2"), (biz_id,))
                bd['ads'] = [dict(a) for a in ads]
                daisy_ctx = {
                    'name': bd.get('name'), 'category': bd.get('category'),
                    'description': bd.get('description'), 'whatsapp': bd.get('whatsapp'),
                    'hours': bd.get('hours') or 'Mon-Sat 8am-7pm',
                    'brand_color': bd.get('brand_color') or '#2b7a78',
                    'photos': [p.strip() for p in str(bd.get('photos') or '').split(',') if p.strip()],
                    'branches': bd.get('branches') or [], 'ads': bd.get('ads') or [],
                }
                def _admin_regen_bg(ctx, bid):
                    result, err = call_daisy('website', context=ctx)
                    html = (result or {}).get('html') if result else None
                    if html:
                        try: db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (html, bid))
                        except Exception as e: print(f"Admin regen save error: {e}")
                    else:
                        print(f"[Daisy API] admin regen failed for biz {bid}: {err}")
                import threading as _threading
                _threading.Thread(target=_admin_regen_bg, args=(daisy_ctx, int(biz_id)), daemon=True).start()
                flash("✅ Daisy is regenerating this site — refresh in about 60 seconds!")
        elif action == 'delete':
            db_execute(q("DELETE FROM business WHERE id=?"), (biz_id,))
        elif action == 'send_to_pool':
            # Owner refused or ignored — save their generated website to the pool
            # so the next business in the same category gets it for free
            biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (biz_id,))
            if biz and biz.get('generated_html') and len(biz['generated_html']) > 2000:
                category = (biz.get('category') or '').lower().strip()
                # Only pool if not already in pool from this business
                already = db_fetchone(
                    q("SELECT id FROM template_pool WHERE category=? AND html=?"),
                    (category, biz['generated_html'])
                )
                if not already:
                    db_insert(
                        q("INSERT INTO template_pool (category, html, quality_score) VALUES (?,?,?)"),
                        (category, biz['generated_html'], 90)
                    )
                    flash(f"✅ '{biz['name']}' website moved to pool — next {category} business gets it free!", 'success')
                else:
                    flash("Already in pool.", 'info')
            else:
                flash("No generated website found for this business — generate one first.", 'error')

        return redirect('/admin')

    businesses = db_fetchall(q("""
        SELECT b.*, u.name as owner_name, COUNT(r.id) as report_count
        FROM business b
        LEFT JOIN users u ON u.id=b.owner_id
        LEFT JOIN reports r ON r.business_id=b.id
        GROUP BY b.id, u.name ORDER BY b.created_at DESC
    """))

    try:
        late_alert = db_fetchall(q("""
            SELECT * FROM business WHERE is_premium=1
            AND (last_payment_date IS NULL OR last_payment_date < CURRENT_DATE - INTERVAL '30 days')
            ORDER BY last_payment_date ASC
        """))
    except:
        late_alert = []

    stats = {
        'total_users':      (db_fetchone(q("SELECT COUNT(*) as c FROM users")) or {}).get('c',0),
        'total_businesses': (db_fetchone(q("SELECT COUNT(*) as c FROM business")) or {}).get('c',0),
        'total_reviews':    (db_fetchone(q("SELECT COUNT(*) as c FROM reviews")) or {}).get('c',0),
        'total_paid':       (db_fetchone(q("SELECT COUNT(*) as c FROM business WHERE plan IS NOT NULL AND plan!='free'")) or {}).get('c',0),
    }

    # Template pool stats — show how many Daisy calls were saved by reuse
    try:
        pool_stats = db_fetchall(q("SELECT category, times_used, quality_score, created_at FROM template_pool ORDER BY times_used DESC"))
        pool_stats = [dict(p) for p in pool_stats]
        pool_saves = sum(p.get('times_used', 0) for p in pool_stats)
    except:
        pool_stats = []
        pool_saves = 0

    # Reports queue — the one thing that still needs a human
    try:
        reported_businesses = db_fetchall(q("SELECT * FROM business WHERE reports > 0 ORDER BY reports DESC"))
        reported_businesses = [biz_to_dict(b) for b in reported_businesses]
    except:
        reported_businesses = []
    reported_count = len(reported_businesses)

    # Agent activity log — submissions now publish automatically, this is
    # just a record of what agents have brought in recently.
    try:
        agent_activity = db_fetchall(q("""
            SELECT b.name as business_name, a.name as agent_name, b.created_at
            FROM business b
            LEFT JOIN agents a ON a.code=b.agent_code
            WHERE b.agent_code IS NOT NULL
            ORDER BY b.created_at DESC LIMIT 20
        """))
        agent_activity = [dict(a) for a in agent_activity]
    except:
        agent_activity = []

    return render_template('admin.html',
        businesses=[biz_to_dict(b) for b in businesses],
        late_alert=[biz_to_dict(b) for b in (late_alert or [])],
        pool_stats=pool_stats,
        pool_saves=pool_saves,
        reported_businesses=reported_businesses,
        reported_count=reported_count,
        agent_activity=agent_activity,
        **stats)

# ── STATIC PAGES ──────────────────────────────────────────────────────────────
@app.route('/privacy')
def privacy():
    return render_template('privacy.html', current_user=get_current_user())

@app.route('/terms')
def terms():
    return render_template('terms.html', current_user=get_current_user())


@app.route('/admin/migrate-db')
@admin_required
def migrate_db():
    try:
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS last_payment_date DATE")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS payment_months_late INTEGER DEFAULT 0")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS free_trial_end DATE")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS slug TEXT")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS hero_price REAL")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS hero_price_label TEXT")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS generated_html TEXT")
        db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS brand_color TEXT DEFAULT '#2b7a78'")
        db_execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_premium INTEGER DEFAULT 0")
        db_execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS chosen_plan TEXT DEFAULT 'free'")
        # Agent-submitted business extras
        try:
            db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'basic'")
        except Exception:
            pass
        try:
            db_execute("ALTER TABLE business ADD COLUMN IF NOT EXISTS location TEXT")
        except Exception:
            pass
        return "Migration done! All columns added."
    except Exception as e:
        return f"Migration error: {e}"


@app.route('/admin/preview/<int:biz_id>')
@admin_required
def admin_preview(biz_id):
    biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (biz_id,))
    if not biz:
        return "Business not found", 404
    bd = biz_to_dict(biz)
    branches = db_fetchall(q("SELECT * FROM branches WHERE business_id=?"), (biz_id,))
    bd['branches'] = [dict(b) for b in branches]
    ads = db_fetchall(q("SELECT * FROM ads WHERE business_id=? AND active=1 LIMIT 2"), (biz_id,))
    bd['ads'] = [dict(a) for a in ads]
    if bd.get('generated_html'):
        return bd['generated_html']
    wa_link = f"https://wa.me/{bd.get('whatsapp','')}"
    fallback_html = _basic_fallback_site(bd, wa_link)
    daisy_ctx = {
        'name': bd.get('name'), 'category': bd.get('category'),
        'description': bd.get('description'), 'whatsapp': bd.get('whatsapp'),
        'hours': bd.get('hours') or 'Mon-Sat 8am-7pm',
        'brand_color': bd.get('brand_color') or '#2b7a78',
        'photos': [p.strip() for p in str(bd.get('photos') or '').split(',') if p.strip()],
        'branches': bd.get('branches') or [], 'ads': bd.get('ads') or [],
    }
    result, err = call_daisy('website', context=daisy_ctx)
    html = (result or {}).get('html') if result else None
    if html:
        try: db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (html, biz_id))
        except: pass
        return html
    print(f"[Daisy API] admin_preview fallback used for biz {biz_id}: {err}")
    return fallback_html


@app.route('/admin/check-payments')
@admin_required
def check_payments():
    from datetime import date
    overdue = db_fetchall(q(
        "SELECT b.*, u.name as owner_name FROM business b "
        "LEFT JOIN users u ON u.id=b.owner_id "
        "WHERE b.is_premium=1 AND b.free_trial_end IS NOT NULL "
        "AND b.free_trial_end < CURRENT_DATE "
        "AND (b.last_payment_date IS NULL OR b.last_payment_date < b.free_trial_end) "
        "ORDER BY b.free_trial_end ASC"
    ))
    reminded = 0
    suspended = 0
    for b in overdue:
        trial_end = b['free_trial_end']
        if hasattr(trial_end, 'date'):
            trial_end = trial_end.date()
        days_overdue = (date.today() - trial_end).days if trial_end else 0
        if days_overdue > 14:
            db_execute(q("UPDATE business SET is_premium=0,status='suspended' WHERE id=?"), (b['id'],))
            if b['owner_id']:
                db_insert(q("INSERT INTO notifications (user_id,message) VALUES (?,?)"),
                    (b['owner_id'], f"Your business has been suspended due to non-payment. Contact us on WhatsApp to reactivate."))
            suspended += 1
        else:
            if b['owner_id']:
                db_insert(q("INSERT INTO notifications (user_id,message) VALUES (?,?)"),
                    (b['owner_id'], f"Payment reminder: Your free month has ended. Pay UGX 7,500 or 15,000 via WhatsApp. You have {14 - days_overdue} days before suspension."))
            reminded += 1
    return f"Done. {reminded} reminded. {suspended} suspended."


@app.route('/about')
def about():
    return render_template('landing.html')

@app.route('/pricing')
def pricing():
    # Public — no login required, so people can see prices before committing.
    return render_template('pricing.html')

@app.route('/daisy')
def daisy_page():
    # Stopgap until Daisy's own pages are transferred into this app —
    # redirect to her current live deployment so this link never 404s.
    # Once her templates + /daisy/chat are wired locally, replace this
    # with render_template('daisy_landing.html') instead.
    return redirect('https://daisy-qg1c.onrender.com/welcome')

@app.route('/agent/set-password', methods=['GET', 'POST'])
def agent_set_password():
    email = request.args.get('email', '')
    biz = None
    if email:
        user = db_fetchone(q("SELECT * FROM users WHERE email=?"), (email,))
        if user:
            biz = db_fetchone(
                q("SELECT * FROM business WHERE owner_id=? AND status='approved' ORDER BY created_at DESC LIMIT 1"),
                (user['id'],)
            )
    if request.method == 'POST':
        email            = request.form.get('email', '').strip().lower()
        password         = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')
        if password != confirm_password:
            flash('Passwords do not match.', 'error')
            return render_template('set_password.html', email=email, biz=biz)
        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('set_password.html', email=email, biz=biz)
        user = db_fetchone(q("SELECT * FROM users WHERE email=?"), (email,))
        if not user:
            flash('Email not found.', 'error')
            return render_template('set_password.html', email=email, biz=biz)
        db_execute(q("UPDATE users SET password=? WHERE id=?"),
            (generate_password_hash(password), user['id']))
        session['user_id'] = user['id']
        flash('Account activated! Welcome to TrustedBiz 🎉', 'success')
        return redirect('/dashboard')
    return render_template('set_password.html', email=email, biz=biz)


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html', current_user=get_current_user()), 404

@app.errorhandler(413)
def too_large(e):
    # Return 200 so Render doesn't intercept — then flash and redirect
    flash("Photos too large. Please use the compression button or choose fewer images.")
    from flask import make_response
    resp = make_response(redirect('/add-business'))
    return resp 
@app.route('/google2c13209b099aea62.html')
def google_verify():
    return "google-site-verification: google2c13209b099aea62"

@app.route('/trustedbiz2026.txt')
def indexnow_key():
    return "trustedbiz2026", 200, {'Content-Type': 'text/plain'}


@app.route('/sitemap.xml')
def sitemap():
    urls = [
        'https://trustedbiz.co.ug/',
        'https://trustedbiz.co.ug/about',
        'https://trustedbiz.co.ug/privacy',
        'https://trustedbiz.co.ug/terms'
    ]

    businesses = db_fetchall(
        q("SELECT slug FROM business WHERE status='approved'")
    )

    for biz in businesses:
        urls.append(f"https://{biz['slug']}.trustedbiz.co.ug")

    xml = '<?xml version="1.0" encoding="UTF-8"?>'
    xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'

    for url in urls:
        xml += f'<url><loc>{url}</loc></url>'

    xml += '</urlset>'

    return app.response_class(
        xml,
        mimetype='application/xml'
    )


@app.route('/robots.txt')
def robots():
    return """
User-agent: *
Allow: /

Sitemap: https://trustedbiz.co.ug/sitemap.xml
""", 200, {'Content-Type': 'text/plain'}

# ── RUN ───────────────────────────────────────────────────────────────────────

# ══════════════════════════════════════════════════════════════════════════════
# HOSTING — custom domains for Daisy-built business sites
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# HOSTING — deploy your own site, or connect a custom domain to a Daisy-built one
# ══════════════════════════════════════════════════════════════════════════════

@app.route('/deploy', methods=['POST'])
@login_required
def deploy_site():
    """Bring-your-own-HTML hosting, the actual 'hosting platform' side of
    TrustedBiz — same idea as Render or Netlify, deploy what you already have.
    Separate from Daisy's business-builder flow, which creates the business
    record itself; this just needs a name and HTML."""
    user        = get_current_user()
    name        = request.form.get('name','').strip()
    category    = request.form.get('category','Website').strip()
    whatsapp    = request.form.get('whatsapp','').strip()
    brand_color = request.form.get('brand_color','#2b7a78').strip()
    description = request.form.get('description','').strip()
    custom_html = request.form.get('custom_html','').strip()
    html_file   = request.files.get('html_file')
    github_url  = request.form.get('github_url','').strip()

    if html_file and html_file.filename.endswith('.html'):
        custom_html = html_file.read().decode('utf-8')

    # Simple GitHub import — pulls index.html from a public repo's default
    # branch. Real push-to-deploy (via a GitHub App + webhook) needs GitHub
    # App credentials that don't exist yet; this covers "connect a repo" for now.
    if github_url and not custom_html:
        try:
            m = re.search(r'github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$', github_url)
            if m:
                owner, repo = m.group(1), m.group(2)
                for branch in ('main', 'master'):
                    r = requests.get(
                        f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/index.html",
                        timeout=10
                    )
                    if r.status_code == 200 and r.text.strip():
                        custom_html = r.text
                        break
                if not custom_html:
                    flash('Could not find an index.html on the main or master branch of that repo.', 'error')
                    return redirect('/dashboard#sites')
            else:
                flash('That doesn\'t look like a valid GitHub repo URL.', 'error')
                return redirect('/dashboard#sites')
        except Exception as e:
            print(f"GitHub import error: {e}")
            flash('Could not fetch that repo. Check the URL and that it\'s public.', 'error')
            return redirect('/dashboard#sites')

    if not name:
        flash('Site name is required.', 'error')
        return redirect('/dashboard#sites')
    if not custom_html:
        flash('Please upload HTML, paste it, or link a public GitHub repo.', 'error')
        return redirect('/dashboard#sites')
    slug   = make_slug(name)
    biz_id = db_insert(
        q("INSERT INTO business (name, category, whatsapp, description, brand_color, slug, owner_id, status, plan, is_premium, generated_html) VALUES (?,?,?,?,?,?,?,?,?,?,?)"),
        (name, category, whatsapp, description, brand_color, slug, user['id'], 'approved', 'free', 0, custom_html)
    )
    ping_google(slug)
    flash(f'🚀 "{name}" is now LIVE at {slug}.trustedbiz.co.ug!', 'success')
    return redirect('/dashboard#sites')


@app.route('/trusthost/request-domain', methods=['POST'])
@login_required
def trusthost_request_domain():
    user          = get_current_user()
    biz_id        = request.form.get('biz_id','')
    custom_domain = request.form.get('custom_domain','').strip().lower()
    custom_domain = custom_domain.replace('https://','').replace('http://','').rstrip('/')
    if biz_id and custom_domain:
        biz = db_fetchone(q("SELECT id FROM business WHERE id=? AND owner_id=?"), (biz_id, user['id']))
        if biz:
            try:
                db_execute(q("UPDATE business SET custom_domain=? WHERE id=?"), (custom_domain, biz_id))
                flash(f'✅ Domain "{custom_domain}" requested! Point your CNAME to trustedbiz.co.ug then wait 24hrs.', 'success')
            except:
                flash('Run /admin/migrate-db first to enable custom domains.', 'error')
        else:
            flash('Site not found.', 'error')
    return redirect('/dashboard#hosting')


@app.route('/daisy/ping', methods=['GET','POST'])
def daisy_ping():
    return jsonify({"status":"alive","name":"Daisy"})


@app.route('/daisy/build', methods=['POST'])
def daisy_build():
    """Thin proxy to Daisy's own API — she does the generating, we just
    relay the request and cache the result so repeat requests in the same
    category can reuse it for free (template_pool)."""
    data    = request.get_json() or {}
    mode    = (data.get('mode') or '').strip()
    ctx     = data.get('context') or {}
    history = data.get('history') or []

    result, err = call_daisy(mode, context=ctx, history=history)
    html = (result or {}).get('html') if result else None

    if not html:
        return jsonify({'html': None, 'error': err or "Daisy is thinking hard on this one. Please try again in a moment.", 'mode': mode}), 503

    try:
        existing = db_fetchone(q("SELECT id FROM template_pool WHERE category=? AND html=?"), (mode, html))
        if not existing:
            db_insert(q("INSERT INTO template_pool (category, html, quality_score) VALUES (?,?,?)"), (mode, html, 80))
    except Exception as e:
        print(f"[Daisy/TemplateSave] {e}")

    return jsonify({'html': html, 'mode': mode})


@app.route('/daisy/save-testimonial', methods=['POST'])
def daisy_save_testimonial():
    """Save a user testimonial to the DB so it can appear on the homepage."""
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if len(text) < 10:
        return jsonify({'saved': False, 'reason': 'too short'})
    _u = get_current_user()
    user_id = _u['id'] if _u else None
    user_name = (_u.get('name') if _u else None) or 'Anonymous'
    try:
        db_insert(q("INSERT INTO daisy_training (input, output) VALUES (?,?)"),
                  (f"[TESTIMONIAL from {user_name}]", text))
        return jsonify({'saved': True})
    except Exception as e:
        print(f'[Testimonial] {e}')
        return jsonify({'saved': False, 'reason': str(e)})

@app.route('/daisy/save-training', methods=['POST'])
def daisy_save_training():
    """Save a Daisy Q&A pair to the database so it persists across restarts."""
    data = request.get_json() or {}
    inp  = (data.get('input') or '').strip()
    out  = (data.get('output') or '').strip()
    if not inp or not out:
        return jsonify({'saved': False, 'reason': 'empty'})
    try:
        # Skip exact duplicates
        existing = db_fetchone(q("SELECT id FROM daisy_training WHERE input=?"), (inp,))
        if existing:
            return jsonify({'saved': False, 'reason': 'duplicate'})
        db_insert(q("INSERT INTO daisy_training (input, output) VALUES (?,?)"), (inp, out))
        total = (db_fetchone(q("SELECT COUNT(*) as c FROM daisy_training")) or {}).get('c', 0)
        return jsonify({'saved': True, 'total': total})
    except Exception as e:
        print(f'[Daisy/SaveTraining] {e}')
        return jsonify({'saved': False, 'reason': str(e)})

@app.route('/admin/export-training')
@admin_required
def export_training():
    """Download all Daisy training data as training_data.json — replace file in repo and redeploy."""
    import json as _j
    rows = db_fetchall(q("SELECT input, output FROM daisy_training ORDER BY id ASC"))
    pairs = [{'input': r['input'], 'output': r['output']} for r in rows]
    from flask import Response
    return Response(
        _j.dumps(pairs, ensure_ascii=False, indent=2),
        mimetype='application/json',
        headers={'Content-Disposition': 'attachment; filename=training_data.json'}
    )

@app.route('/daisy/chat', methods=['POST'])
def daisy_chat():
    """Thin proxy to Daisy's own API for the conversational flow."""
    data    = request.get_json() or {}
    msg     = (data.get('message') or data.get('user_input') or '').strip()
    history = data.get('history', [])
    has_img = data.get('has_image', False)
    if not msg:
        return jsonify({'reply': 'What would you like to build today?', 'done': False})

    result, err = call_daisy('chat', context={'has_image': has_img},
                              history=history, message=msg, timeout=20)
    if not result:
        return jsonify({'reply': err or "I'm having a small moment — try again!", 'done': False})

    return jsonify({
        'reply': result.get('reply', ''),
        'done':  bool(result.get('mode')),
        'mode':  result.get('mode'),
    })


@app.route('/search')
def search_page():
    q_str = request.args.get('q','')
    results = []
    if q_str:
        import re as _re
        pattern = '%' + q_str + '%'
        results = db_fetchall(
            q("SELECT name,slug,category,description,whatsapp FROM business WHERE status=\'approved\' AND (name LIKE ? OR category LIKE ? OR description LIKE ?) LIMIT 30"),
            (pattern, pattern, pattern)
        )
    return render_template('search.html',
        results=results, query=q_str,
        current_user=get_current_user())



# ══════════════════════════════════════════════════════════════════════════════
# DAISY VIDEO ENGINE ROUTES
# ══════════════════════════════════════════════════════════════════════════════
VIDEO_UPLOAD_DIR = Path("static/video_uploads")
VIDEO_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_VIDEO_EXT = {"mp4","mov","avi","mkv","webm","3gp","m4v"}

def _allowed_video(fn):
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_VIDEO_EXT

@app.route("/daisy/video/upload", methods=["POST"])
def daisy_video_upload():
    from video_engine import submit_video_job
    user_prompt = (request.form.get("prompt") or request.form.get("message") or "").strip()
    video_file  = request.files.get("video")
    if not video_file or not video_file.filename:
        return jsonify({"error":"No video file uploaded"}), 400
    if not _allowed_video(video_file.filename):
        return jsonify({"error":"File type not supported. Use MP4, MOV, or 3GP"}), 400
    if not user_prompt:
        user_prompt = "Make a professional promo video"
    ext      = video_file.filename.rsplit(".",1)[1].lower()
    raw_path = VIDEO_UPLOAD_DIR / f"{secrets.token_hex(10)}.{ext}"
    try:
        video_file.save(str(raw_path))
    except Exception as e:
        return jsonify({"error":f"Upload failed: {e}"}), 500
    size_mb = raw_path.stat().st_size / (1024*1024)
    if size_mb > 150:
        raw_path.unlink(missing_ok=True)
        return jsonify({"error":"Video too large. Max 150MB"}), 400
    job_id = submit_video_job(str(raw_path), user_prompt)
    return jsonify({"job_id":job_id,"status":"processing",
        "message":"Daisy is editing your video... check back in 30-60 seconds 🎬",
        "prompt":user_prompt})

@app.route("/daisy/video/status/<job_id>", methods=["GET"])
def daisy_video_status(job_id):
    from video_engine import get_job
    job = get_job(job_id)
    if not job:
        return jsonify({"error":"Job not found"}), 404
    resp = {"job_id":job_id,"status":job["status"],"prompt":job.get("prompt","")}
    if job["status"] == "done":
        resp["url"]     = job["url"]
        resp["plan"]    = job.get("plan",{})
        resp["message"] = "Your video is ready! 🎬✨"
    elif job["status"] == "error":
        resp["error"]   = job.get("error","Unknown error")
        resp["message"] = "Something went wrong. Try again with a shorter video."
    else:
        resp["message"] = "Still editing... Daisy is working on it 🎬"
    return jsonify(resp)

@app.route("/daisy/video/check", methods=["GET"])
def daisy_video_check():
    import shutil as _sh
    ff = _sh.which("ffmpeg")
    fp = _sh.which("ffprobe")
    return jsonify({
        "ffmpeg":      bool(ff),
        "ffprobe":     bool(fp),
        "ffmpeg_path": ff,
        "cloudinary":  bool(os.environ.get("CLOUDINARY_URL")),
        "anthropic":   bool(os.environ.get("ANTHROPIC_API_KEY")),
        "ready":       bool(ff and fp),
        "message":     "Daisy Video Engine is ready 🎬" if ff else "FFmpeg not found — check build command"
    })

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)


# ══════════════════════════════════════════════════════════════════════════════
# AGENT SYSTEM
# ══════════════════════════════════════════════════════════════════════════════

def agent_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'agent_id' not in session:
            return redirect('/agent/login')
        return f(*args, **kwargs)
    return decorated

def get_current_agent():
    if 'agent_id' in session:
        return db_fetchone(q("SELECT * FROM agents WHERE id=?"), (session['agent_id'],))
    return None

def make_agent_code():
    """Generate a unique AGT-XXXX code."""
    while True:
        code = "AGT-" + str(secrets.randbelow(9000) + 1000)
        existing = db_fetchone(q("SELECT id FROM agents WHERE code=?"), (code,))
        if not existing:
            return code


@app.route('/agent/register', methods=['GET', 'POST'])
def agent_register():
    if request.method == 'POST':
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        whatsapp = request.form.get('whatsapp', '').strip()
        area     = request.form.get('area', '').strip()

        if not all([name, email, password, whatsapp, area]):
            flash('Please fill in all fields.', 'error')
            return render_template('agent_register.html')

        existing = db_fetchone(q("SELECT id FROM agents WHERE email=?"), (email,))
        if existing:
            flash('An agent account with that email already exists.', 'error')
            return render_template('agent_register.html')

        code     = make_agent_code()
        hashed   = generate_password_hash(password)
        agent_id = db_insert(
            q("INSERT INTO agents (name, email, password, whatsapp, area, code) VALUES (?,?,?,?,?,?)"),
            (name, email, hashed, whatsapp, area, code)
        )

        # Notify admin on WhatsApp
        admin_wa = os.environ.get('ADMIN_WHATSAPP', '256753187966')
        _send_wa_notification = f"New TrustedBiz Agent registered!\nName: {name}\nEmail: {email}\nArea: {area}\nCode: {code}\nWhatsApp: {whatsapp}"
        # (WhatsApp notification to admin — plug in your SMS/WA API here)

        session['agent_id'] = agent_id
        flash(f'Welcome {name}! Your agent code is {code}. Start adding businesses!', 'success')
        return redirect('/agent/dashboard')

    return render_template('agent_register.html')


@app.route('/agent/login', methods=['GET', 'POST'])
def agent_login():
    if request.method == 'POST':
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        agent = db_fetchone(q("SELECT * FROM agents WHERE email=?"), (email,))
        if not agent or not check_password_hash(agent['password'], password):
            flash('Invalid email or password.', 'error')
            return render_template('agent_login.html')

        session['agent_id'] = agent['id']
        return redirect('/agent/dashboard')

    return render_template('agent_login.html')


@app.route('/agent/logout')
def agent_logout():
    session.pop('agent_id', None)
    return redirect('/agent/login')


@app.route('/agent/dashboard')
@agent_login_required
def agent_dashboard():
    agent = get_current_agent()
    if not agent:
        return redirect('/agent/login')

    businesses = db_fetchall(
        q("SELECT * FROM business WHERE agent_code=? ORDER BY created_at DESC"),
        (agent['code'],)
    )

    approved = [b for b in businesses if b['status'] == 'approved']
    pending  = [b for b in businesses if b['status'] == 'pending']

    stats = {
        'total':        len(businesses),
        'approved':     len(approved),
        'pending':      len(pending),
        'earnings':     len(approved) * 1000,
        'total_earned': len(approved) * 1000,  # expand with real payout tracking later
    }

    # Fetch invite codes for approved businesses so agent can share passcodes
    invite_codes = {}
    for biz in businesses:
        if biz['status'] == 'approved':
            inv = db_fetchone(
                q("SELECT * FROM invite_codes WHERE biz_id=? ORDER BY id DESC LIMIT 1"),
                (biz['id'],)
            )
            if inv:
                invite_codes[biz['id']] = dict(inv)

    # Attach plan to each biz dict (from column if exists, else derive from is_premium)
    biz_dicts = []
    for biz in businesses:
        b = dict(biz)
        if not b.get('plan'):
            b['plan'] = 'promax' if b.get('is_premium') else 'free'
        biz_dicts.append(b)

    return render_template('agent_dashboard.html',
        agent=dict(agent),
        businesses=biz_dicts,
        stats=stats,
        invite_codes=invite_codes
    )


@app.route('/agent/add-business', methods=['POST'])
@agent_login_required
def agent_add_business():
    agent = get_current_agent()
    if not agent:
        return redirect('/agent/login')

    name        = request.form.get('name', '').strip()
    category    = request.form.get('category', '').strip()
    whatsapp    = request.form.get('whatsapp', '').strip()
    email       = request.form.get('email', '').strip().lower()
    location    = request.form.get('location', '').strip()
    hours       = request.form.get('hours', 'Mon–Sat 8am–6pm').strip() or 'Mon–Sat 8am–6pm'
    description = request.form.get('description', '').strip()
    brand_color = request.form.get('brand_color', '#2b7a78').strip()
    map_link    = request.form.get('map_link', '').strip()
    plan        = request.form.get('plan', 'basic').strip().lower()
    if plan not in ('free', 'basic', 'promax'):
        plan = 'basic'

    # Only name, category, location, description are required — email/whatsapp optional for demo
    if not all([name, category, location, description]):
        flash('Please fill in the business name, category, location and description.', 'error')
        return redirect('/agent/dashboard')

    # Create a placeholder user only if email was given
    owner_id = 0
    if email:
        existing_user = db_fetchone(q("SELECT id FROM users WHERE email=?"), (email,))
        if existing_user:
            owner_id = existing_user['id']
        else:
            temp_password = generate_password_hash(secrets.token_urlsafe(8))
            owner_id = db_insert(
                q("INSERT INTO users (name, email, password) VALUES (?,?,?)"),
                (name, email, temp_password)
            )

    slug       = make_slug(name)
    agent_code = agent['code']
    is_premium = 1 if plan in ('basic', 'promax') else 0

    # Store plan in the business record (reuse a spare column or add to notes)
    # We store plan value in a separate field; fall back to storing in description prefix if column missing
    try:
        biz_id = db_insert(
            q("INSERT INTO business (name, category, whatsapp, description, hours, brand_color, slug, owner_id, status, agent_code, is_premium, plan) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)"),
            (name, category, whatsapp, description, hours, brand_color, slug, owner_id, 'pending', agent_code, is_premium, plan)
        )
    except Exception:
        # plan column may not exist yet — fallback without it
        biz_id = db_insert(
            q("INSERT INTO business (name, category, whatsapp, description, hours, brand_color, slug, owner_id, status, agent_code, is_premium) VALUES (?,?,?,?,?,?,?,?,?,?,?)"),
            (name, category, whatsapp, description, hours, brand_color, slug, owner_id, 'pending', agent_code, is_premium)
        )

    # Save location (stored in a notes/location field if available, otherwise skip gracefully)
    try:
        db_execute(q("UPDATE business SET location=? WHERE id=?"), (location, biz_id))
    except Exception:
        pass

    # Save photos if uploaded
    photos = request.files.getlist('photos')
    if photos and photos[0].filename:
        try:
            photo_refs = save_photos(photos)
            if photo_refs:
                db_execute(q("UPDATE business SET photos=? WHERE id=?"), (','.join(photo_refs), biz_id))
        except Exception:
            pass

    plan_label = {'free': 'Free', 'basic': 'Basic', 'promax': 'Pro Max'}.get(plan, plan.title())
    flash(f'✅ "{name}" ({plan_label} plan) submitted for approval! Once approved, you\'ll get a passcode to share with the owner.', 'success')
    return redirect('/agent/dashboard')


# ── ADMIN: Approve agent-submitted business and notify owner ──────────────────

@app.route('/admin/approve-agent-biz/<int:biz_id>', methods=['POST'])
@admin_required
def admin_approve_agent_biz(biz_id):
    biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (biz_id,))
    if not biz:
        return "Not found", 404

    db_execute(q("UPDATE business SET status='approved', verified=1 WHERE id=?"), (biz_id,))

    category = (biz.get('category') or '').lower().strip()

    # ── WEBSITE GENERATION ────────────────────────────────────────────────────
    # NOTE: the old pool-swap optimization (reusing a template's HTML and
    # string-swapping in the new business's name/contact info) lived inside
    # ai_generator.swap_business_info, which isn't available now that Daisy
    # is a separate service — that function's logic wasn't visible to build
    # a safe equivalent here. For now every business gets a fresh Daisy
    # build; times_used still increments so pool stats stay meaningful, and
    # a real swap-on-Daisy's-side is worth adding back once her API exists.
    import threading as _t
    biz_plan = (biz.get('plan') or 'free')

    pooled = db_fetchone(
        q("SELECT * FROM template_pool WHERE category=? AND times_used=0 ORDER BY quality_score DESC LIMIT 1"),
        (category,)
    )

    def _agent_gen(biz_dict, bid, cat, bplan, pool_id):
        daisy_ctx = {
            'name': biz_dict.get('name'), 'category': biz_dict.get('category'),
            'description': biz_dict.get('description'), 'whatsapp': biz_dict.get('whatsapp'),
            'hours': biz_dict.get('hours') or 'Mon-Sat 8am-7pm',
            'brand_color': biz_dict.get('brand_color') or '#2b7a78', 'plan': bplan,
        }
        result, err = call_daisy('website', context=daisy_ctx)
        html = (result or {}).get('html') if result else None
        if html and len(html) > 2000:
            db_execute(q("UPDATE business SET generated_html=? WHERE id=?"), (html, bid))
            if pool_id:
                db_execute(q("UPDATE template_pool SET times_used=times_used+1 WHERE id=?"), (pool_id,))
            else:
                db_insert(q("INSERT INTO template_pool (category, html, quality_score, times_used) VALUES (?,?,?,0)"),
                          (cat, html, 100))
            print(f"Daisy gen done for biz_id={bid} (category={cat})")
        else:
            print(f"[Daisy API] gen failed for biz_id={bid}: {err}")

    bd = biz_to_dict(biz)
    bd['branches'] = []
    bd['ads'] = []
    _t.Thread(target=_agent_gen, args=(bd, biz_id, category, biz_plan, pooled['id'] if pooled else None), daemon=True).start()
    ping_google(biz.get('slug', ''))
    flash(f"✅ '{biz['name']}' approved — Daisy is building the website now!", 'success')

    # ── INVITE CODE: generate one so the agent can give the owner dashboard access
    import string, secrets as _sec
    def _make_code():
        chars = string.ascii_uppercase + string.digits
        for _ in range(100):
            code = ''.join(_sec.choice(chars) for _ in range(6))
            if not db_fetchone(q("SELECT id FROM invite_codes WHERE code=?"), (code,)):
                return code
        return _sec.token_hex(3).upper()

    actual_plan = biz.get('plan') or 'basic'
    existing_code = db_fetchone(q("SELECT * FROM invite_codes WHERE biz_id=? AND used=0"), (biz_id,))
    if not existing_code:
        agent_row = db_fetchone(q("SELECT * FROM agents WHERE code=?"), (biz.get('agent_code'),)) if biz.get('agent_code') else None
        agent_id = agent_row['id'] if agent_row else 0
        invite_code_str = _make_code()
        db_insert(
            q("INSERT INTO invite_codes (code, biz_id, agent_id, plan) VALUES (?,?,?,?)"),
            (invite_code_str, biz_id, agent_id, actual_plan)
        )
    else:
        invite_code_str = existing_code['code']

    # Notify the agent with the invite code + direct link to share with the owner
    if biz.get('agent_code'):
        agent_row = db_fetchone(q("SELECT * FROM agents WHERE code=?"), (biz['agent_code'],))
        if agent_row:
            site_url  = f"https://{biz['slug']}.trustedbiz.co.ug"
            join_link = f"https://trustedbiz.co.ug/join?code={invite_code_str}"
            db_insert(
                q("INSERT INTO notifications (user_id, message) VALUES (?,?)"),
                (0, f"🎉 '{biz['name']}' APPROVED! Show the owner their website: {site_url} — passcode to share: {invite_code_str} — activation link: {join_link}")
            )

    return redirect('/admin')


@app.route('/join', methods=['GET', 'POST'])
def join_with_code():
    """Business owner enters invite code → creates account → gets dashboard access."""
    code = request.args.get('code', '').strip().upper()
    invite = None
    biz = None

    if code:
        invite = db_fetchone(q("SELECT * FROM invite_codes WHERE code=? AND used=0"), (code,))
        if invite:
            biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (invite['biz_id'],))
        else:
            flash("That invite code is invalid or already used. Contact your TrustedBiz agent for a new one.", "error")

    if request.method == 'POST':
        code     = request.form.get('code', '').strip().upper()
        name     = request.form.get('name', '').strip()
        email    = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm  = request.form.get('confirm', '')

        invite = db_fetchone(q("SELECT * FROM invite_codes WHERE code=? AND used=0"), (code,))
        if not invite:
            flash("Invalid or already used invite code. Contact your TrustedBiz agent.", "error")
            return render_template('join.html', code=code, invite=None, biz=None)

        biz = db_fetchone(q("SELECT * FROM business WHERE id=?"), (invite['biz_id'],))

        if password != confirm:
            flash("Passwords do not match.", "error")
            return render_template('join.html', code=code, invite=dict(invite), biz=biz_to_dict(biz) if biz else None)
        if len(password) < 6:
            flash("Password must be at least 6 characters.", "error")
            return render_template('join.html', code=code, invite=dict(invite), biz=biz_to_dict(biz) if biz else None)

        # Create or update user
        existing_user = db_fetchone(q("SELECT * FROM users WHERE email=?"), (email,))
        if existing_user:
            user_id = existing_user['id']
            db_execute(q("UPDATE users SET password=?, name=? WHERE id=?"),
                       (generate_password_hash(password), name, user_id))
        else:
            user_id = db_insert(
                q("INSERT INTO users (name, email, password, is_premium) VALUES (?,?,?,1)"),
                (name, email, generate_password_hash(password))
            )

        # Link user to the business
        db_execute(q("UPDATE business SET owner_id=? WHERE id=?"), (user_id, biz['id']))
        # Mark code as used
        db_execute(q("UPDATE invite_codes SET used=1, used_by_user_id=? WHERE code=?"), (user_id, code))
        # Log them in
        session['user_id'] = user_id
        flash(f"🎉 Welcome to TrustedBiz! Your business dashboard is ready.", "success")
        return redirect('/dashboard')

    return render_template('join.html', code=code, invite=dict(invite) if invite else None,
                           biz=biz_to_dict(biz) if biz else None)


@app.route('/agent/generate-invite/<int:biz_id>', methods=['POST'])
@agent_login_required
def agent_generate_invite(biz_id):
    """Agent generates a unique invite code for a business they submitted."""
    agent = get_current_agent()
    biz = db_fetchone(q("SELECT * FROM business WHERE id=? AND agent_code=?"), (biz_id, agent['code']))
    if not biz:
        flash("Business not found.", "error")
        return redirect('/agent/dashboard')

    existing = db_fetchone(q("SELECT * FROM invite_codes WHERE biz_id=? AND used=0"), (biz_id,))
    if existing:
        join_link = f"https://trustedbiz.co.ug/join?code={existing['code']}"
        flash(f"Invite code: {existing['code']} — Share this link: {join_link}", "success")
        return redirect('/agent/dashboard')

    flash("This business hasn't been approved by admin yet. Check back soon.", "warning")
    return redirect('/agent/dashboard')


# ── MIGRATE: add agent columns if upgrading existing DB ──────────────────────
@app.route('/admin/migrate-agents')
@admin_required
def migrate_agents():
    try:
        db_execute("CREATE TABLE IF NOT EXISTS agents (id SERIAL PRIMARY KEY, name TEXT, email TEXT UNIQUE, password TEXT, whatsapp TEXT, area TEXT, code TEXT UNIQUE, status TEXT DEFAULT 'active', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
    except: pass
    try:
        db_execute(q("ALTER TABLE business ADD COLUMN agent_code TEXT"))
    except: pass
    try:
        db_execute(q("ALTER TABLE business ADD COLUMN plan TEXT DEFAULT 'basic'"))
    except: pass
    try:
        db_execute(q("ALTER TABLE business ADD COLUMN location TEXT"))
    except: pass
    return "Agent migration done!"

