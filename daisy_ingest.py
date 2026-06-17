"""
DAISY INGESTION ENGINE — Phase 1
=================================
Fetches a webpage, extracts clean knowledge pairs,
and writes them DIRECTLY into processing-law-ai.jsx FLAT_DICT.

No database. No cloud. The file grows. Daisy grows.

Usage:
  python daisy_ingest.py --url "https://en.wikipedia.org/wiki/Photosynthesis"
  python daisy_ingest.py --auto   (runs scheduler every 60 seconds)

Requirements:
  pip install requests beautifulsoup4 schedule
"""

import re
import os
import json
import time
import argparse
import requests
import schedule
import subprocess
from bs4 import BeautifulSoup
from datetime import datetime

# ============================================================
# CONFIG — Edit these paths to match your project
# ============================================================
JSX_FILE_PATH = "processing-law-ai.jsx"   # Path to your Daisy JSX file
LOG_FILE_PATH = "daisy_ingest.log"        # Log of everything ingested

# ============================================================
# GIT AUTO-PUSH CONFIG
# Daisy pushes her own learning back to GitHub so words
# survive Render restarts. Set GITHUB_TOKEN in Render env vars.
# ============================================================
GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_URL = os.environ.get("GITHUB_REPO_URL", "")
# e.g. "https://github.com/yourusername/daisy-repo.git"
# Render will use: https://TOKEN@github.com/yourusername/daisy-repo.git

GIT_USER_NAME  = "Daisy"
GIT_USER_EMAIL = "daisy@trustedbiz.co.ug"


# Seed URLs — Daisy will rotate through these automatically
SEED_URLS = [
    "https://en.wikipedia.org/wiki/Artificial_intelligence",
    "https://en.wikipedia.org/wiki/Computer_science",
    "https://en.wikipedia.org/wiki/Biology",
    "https://en.wikipedia.org/wiki/Physics",
    "https://en.wikipedia.org/wiki/Mathematics",
    "https://en.wikipedia.org/wiki/History_of_Uganda",
    "https://en.wikipedia.org/wiki/Economics",
    "https://en.wikipedia.org/wiki/Psychology",
    "https://en.wikipedia.org/wiki/Chemistry",
    "https://en.wikipedia.org/wiki/Geography",
    "https://en.wikipedia.org/wiki/Technology",
    "https://en.wikipedia.org/wiki/Medicine",
    "https://en.wikipedia.org/wiki/Philosophy",
    "https://en.wikipedia.org/wiki/Astronomy",
    "https://en.wikipedia.org/wiki/Climate_change",
]

# Tracks which URL to fetch next
_url_index = 0


# ============================================================
# STEP 1 — FETCH & CLEAN
# ============================================================
def fetch_page(url):
    """Fetch a webpage and return clean text."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (DaisyBot/1.0)"}
        res = requests.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header",
                          "aside", "figure", "table", ".navbox", ".infobox"]):
            tag.decompose()

        # Get clean paragraphs
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)

        # Strip references like [1], [2]
        text = re.sub(r"\[\d+\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()

        return text
    except Exception as e:
        log(f"FETCH ERROR: {url} — {e}")
        return None


# ============================================================
# STEP 2 — EXTRACT PAIRS (word → definition)
# ============================================================
def extract_pairs(text, max_pairs=30):
    """
    Extract word:definition pairs from raw text.
    Looks for sentences that define or describe things.
    Pattern: "[Term] is/are/refers to [explanation]."
    """
    pairs = {}

    # Split into sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)

    define_pattern = re.compile(
        r"^([A-Z][a-zA-Z\s\-]{2,40})\s+(?:is|are|refers to|means|can be defined as|was|were)\s+(.{20,120})\.",
        re.IGNORECASE
    )

    for sentence in sentences:
        sentence = sentence.strip()
        match = define_pattern.match(sentence)
        if match:
            raw_term = match.group(1).strip()
            definition = match.group(2).strip()

            # Clean the term into dictionary key format
            key = raw_term.lower()
            key = re.sub(r"[^a-z0-9\s]", "", key)
            key = re.sub(r"\s+", "_", key).strip("_")

            # Filter out bad keys
            if len(key) < 3 or len(key) > 50:
                continue
            if key in ("the", "a", "an", "it", "this", "that", "they"):
                continue

            # Clean definition — one sentence, lowercase start
            definition = definition.rstrip(".")
            definition = definition[0].upper() + definition[1:] if definition else definition

            if key and definition:
                pairs[key] = definition
                if len(pairs) >= max_pairs:
                    break

    return pairs


# ============================================================
# STEP 3 — READ EXISTING FLAT_DICT KEYS
# ============================================================
def get_existing_keys(jsx_path):
    """Read all existing keys from FLAT_DICT to avoid duplicates."""
    try:
        with open(jsx_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Match keys like:   some_key: "definition",
        keys = re.findall(r'^\s{2}([a-z][a-z0-9_]+):\s*"', content, re.MULTILINE)
        return set(keys)
    except Exception as e:
        log(f"READ ERROR: {e}")
        return set()


# ============================================================
# STEP 4 — WRITE DIRECTLY INTO JSX FILE
# ============================================================
def write_to_jsx(jsx_path, new_pairs):
    """
    Inject new pairs directly into FLAT_DICT inside the JSX file.
    Finds the closing }; of FLAT_DICT and inserts before it.
    """
    if not new_pairs:
        log("No new pairs to write.")
        return 0

    try:
        with open(jsx_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Find the FLAT_DICT closing marker
        # We look for the end of FLAT_DICT — the line with just "};"
        # that comes after the FLAT_DICT declaration
        flat_dict_start = content.find("const FLAT_DICT = {")
        if flat_dict_start == -1:
            log("ERROR: Could not find FLAT_DICT in JSX file.")
            return 0

        # Find the closing }; after FLAT_DICT starts
        search_from = flat_dict_start + len("const FLAT_DICT = {")
        closing_pos = content.find("\n};", search_from)
        if closing_pos == -1:
            log("ERROR: Could not find end of FLAT_DICT.")
            return 0

        # Build the new entries string
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_lines = f"\n  // === INGESTED {timestamp} ===\n"
        for key, definition in new_pairs.items():
            # Escape any quotes in definition
            safe_def = definition.replace('"', "'")
            new_lines += f'  {key}: "{safe_def}",\n'

        # Insert before the closing };
        new_content = content[:closing_pos] + new_lines + content[closing_pos:]

        with open(jsx_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        log(f"WROTE {len(new_pairs)} new entries into {jsx_path}")
        return len(new_pairs)

    except Exception as e:
        log(f"WRITE ERROR: {e}")
        return 0


# ============================================================
# STEP 5 — FILTER OUT ALREADY-KNOWN WORDS
# ============================================================
def filter_new_only(pairs, existing_keys):
    """Remove any pairs Daisy already knows."""
    return {k: v for k, v in pairs.items() if k not in existing_keys}


# ============================================================
# GIT AUTO-PUSH — Daisy commits her own learning to GitHub
# Words survive Render restarts because they live in the repo
# ============================================================
def git_push(word_count):
    """Push updated JSX file back to GitHub after each ingest cycle."""
    if not GITHUB_TOKEN or not GITHUB_REPO_URL:
        log("GIT PUSH SKIPPED: GITHUB_TOKEN or GITHUB_REPO_URL not set.")
        return

    try:
        repo_dir = os.path.dirname(os.path.abspath(JSX_FILE_PATH)) or "."

        # Build authenticated remote URL
        # e.g. https://TOKEN@github.com/user/repo.git
        if "https://" in GITHUB_REPO_URL:
            auth_url = GITHUB_REPO_URL.replace(
                "https://", f"https://{GITHUB_TOKEN}@"
            )
        else:
            auth_url = GITHUB_REPO_URL

        env = {
            **os.environ,
            "GIT_AUTHOR_NAME":     GIT_USER_NAME,
            "GIT_AUTHOR_EMAIL":    GIT_USER_EMAIL,
            "GIT_COMMITTER_NAME":  GIT_USER_NAME,
            "GIT_COMMITTER_EMAIL": GIT_USER_EMAIL,
        }

        def run(cmd):
            result = subprocess.run(
                cmd, cwd=repo_dir, env=env,
                capture_output=True, text=True
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()

        # Stage the JSX file
        code, out, err = run(["git", "add", JSX_FILE_PATH])
        if code != 0:
            log(f"GIT ADD failed: {err}")
            return

        # Check if there's actually anything to commit
        code, out, err = run(["git", "diff", "--cached", "--quiet"])
        if code == 0:
            log("GIT: Nothing new to commit.")
            return

        # Commit
        msg = f"Daisy learned {word_count} new words [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        code, out, err = run(["git", "commit", "-m", msg])
        if code != 0:
            log(f"GIT COMMIT failed: {err}")
            return

        # Push
        code, out, err = run(["git", "push", auth_url, "main"])
        if code == 0:
            log(f"GIT PUSH SUCCESS — Daisy's words saved to GitHub.")
        else:
            # Try 'master' branch as fallback
            code, out, err = run(["git", "push", auth_url, "master"])
            if code == 0:
                log(f"GIT PUSH SUCCESS (master) — Daisy's words saved.")
            else:
                log(f"GIT PUSH failed: {err}")

    except Exception as e:
        log(f"GIT PUSH ERROR: {e}")


# ============================================================
# MAIN INGEST CYCLE
# ============================================================
def ingest_one(url=None):
    """Run one full ingestion cycle."""
    global _url_index

    # Pick URL
    if not url:
        url = SEED_URLS[_url_index % len(SEED_URLS)]
        _url_index += 1

    log(f"--- INGESTING: {url}")

    # Fetch
    text = fetch_page(url)
    if not text:
        log("SKIP: Empty page.")
        return

    # Extract
    pairs = extract_pairs(text, max_pairs=40)
    log(f"EXTRACTED: {len(pairs)} raw pairs")

    # Filter
    existing = get_existing_keys(JSX_FILE_PATH)
    new_pairs = filter_new_only(pairs, existing)
    log(f"NEW (not in Daisy yet): {len(new_pairs)} pairs")

    if not new_pairs:
        log("Daisy already knows all of these. Moving on.")
        return

    # Write
    written = write_to_jsx(JSX_FILE_PATH, new_pairs)
    log(f"SUCCESS: {written} words added to Daisy.")
    log(f"Daisy total keys approx: {len(existing) + written}")

    # Push to GitHub so words survive Render restarts
    git_push(written)


# ============================================================
# SCHEDULER — Runs automatically every N minutes
# ============================================================
def run_scheduler(interval_minutes=1):
    """Run ingestion on a schedule. Daisy learns continuously."""
    log(f"=== DAISY INGESTION ENGINE STARTED ===")
    log(f"Interval: every {interval_minutes} minute(s)")
    log(f"Target file: {JSX_FILE_PATH}")
    log(f"Seed URLs: {len(SEED_URLS)} sources")

    # Run immediately first
    ingest_one()

    # Then schedule
    schedule.every(interval_minutes).minutes.do(ingest_one)

    while True:
        schedule.run_pending()
        time.sleep(10)


# ============================================================
# LOGGING
# ============================================================
def log(message):
    timestamp = datetime.now().strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    print(line)
    try:
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daisy Ingestion Engine")
    parser.add_argument("--url", type=str, help="Ingest a specific URL once")
    parser.add_argument("--auto", action="store_true", help="Run scheduler continuously")
    parser.add_argument("--interval", type=int, default=1, help="Minutes between ingestion cycles (default: 1)")
    args = parser.parse_args()

    if not os.path.exists(JSX_FILE_PATH):
        print(f"ERROR: JSX file not found at '{JSX_FILE_PATH}'")
        print("Set JSX_FILE_PATH at the top of this script to match your project.")
        exit(1)

    if args.url:
        # One-shot ingest of a specific URL
        ingest_one(url=args.url)
    elif args.auto:
        # Continuous scheduler
        run_scheduler(interval_minutes=args.interval)
    else:
        # Default: run once through the seed list
        ingest_one()


# ============================================================
# FLASK INTEGRATION — Phase 3
# Add this to your existing Flask app:
#
#   from daisy_ingest import init_daisy, daisy_bp
#   app.register_blueprint(daisy_bp)
#   init_daisy(app)
#
# Routes added:
#   GET  /daisy/status   — word count, last ingest time, log tail
#   POST /daisy/ingest   — trigger one manual ingest cycle
#   POST /daisy/ingest?url=https://... — ingest a specific URL
# ============================================================

import threading
from flask import Blueprint, jsonify, request

daisy_bp = Blueprint("daisy", __name__)

# Shared state
_ingest_thread = None
_last_ingest = None
_ingest_count = 0
_running = False


def _background_scheduler(interval_minutes):
    """Runs in a daemon thread. Ingests on a loop forever."""
    global _last_ingest, _ingest_count, _running
    _running = True
    log(f"=== DAISY BACKGROUND SCHEDULER STARTED (every {interval_minutes}m) ===")
    while _running:
        try:
            ingest_one()
            _last_ingest = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _ingest_count += 1
        except Exception as e:
            log(f"SCHEDULER ERROR: {e}")
        # Sleep in small chunks so it can be stopped cleanly
        for _ in range(interval_minutes * 60):
            if not _running:
                break
            time.sleep(1)


def init_daisy(app, interval_minutes=2):
    """
    Call this once in your Flask app after creating app.
    Starts the background ingestion thread automatically.

    Example:
        app = Flask(__name__)
        init_daisy(app)
    """
    global _ingest_thread

    if not os.path.exists(JSX_FILE_PATH):
        print(f"[DAISY] WARNING: JSX file not found at '{JSX_FILE_PATH}'. Ingestion paused.")
        return

    _ingest_thread = threading.Thread(
        target=_background_scheduler,
        args=(interval_minutes,),
        daemon=True  # Dies automatically when Flask stops
    )
    _ingest_thread.start()
    print(f"[DAISY] Ingestion engine running — every {interval_minutes} minute(s)")


@daisy_bp.route("/daisy/status", methods=["GET"])
def daisy_status():
    """Returns Daisy's current knowledge stats."""
    existing = get_existing_keys(JSX_FILE_PATH)

    # Read last 10 lines of log
    log_tail = []
    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_tail = [l.strip() for l in lines[-10:]]
    except:
        pass

    return jsonify({
        "status": "running" if _running else "stopped",
        "words_in_daisy": len(existing),
        "ingest_cycles_completed": _ingest_count,
        "last_ingest": _last_ingest or "not yet",
        "jsx_file": JSX_FILE_PATH,
        "seed_urls": len(SEED_URLS),
        "log_tail": log_tail
    })


@daisy_bp.route("/daisy/ingest", methods=["POST"])
def daisy_ingest_now():
    """Manually trigger one ingest cycle. Optional ?url= param."""
    url = request.args.get("url", None)
    try:
        ingest_one(url=url)
        existing = get_existing_keys(JSX_FILE_PATH)
        return jsonify({
            "success": True,
            "url_ingested": url or "next seed url",
            "words_in_daisy": len(existing),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
