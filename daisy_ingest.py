"""
DAISY INGESTION ENGINE — v2 (Self-Expanding Crawler)
=====================================================
What changed from v1:
  - No more fixed seed list that gets exhausted
  - Daisy now CRAWLS Wikipedia — follows links to new pages forever
  - Smart queue: discovered URLs go into a crawl queue
  - Queue is saved to disk so it survives Render restarts
  - Visited URLs are tracked so nothing is ingested twice
  - Rich extraction: tries to build what_it_does + examples too
  - Starts from 200 seed topics across science, Africa, tech, life
  - Self-feeding: every page discovered adds ~10 new URLs to queue

Result: Daisy never runs dry. Queue grows faster than it empties.
"""

import re
import os
import json
import time
import random
import argparse
import requests
import threading
import subprocess
from bs4 import BeautifulSoup
from datetime import datetime
from flask import Blueprint, jsonify, request

# ============================================================
# CONFIG
# ============================================================
JSX_FILE_PATH   = "processing-law-ai.jsx"
LOG_FILE_PATH   = "daisy_ingest.log"
QUEUE_FILE      = "daisy_queue.json"       # crawl queue — survives restarts
VISITED_FILE    = "daisy_visited.json"     # visited URLs — no re-scraping

GITHUB_TOKEN    = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO_URL = os.environ.get("GITHUB_REPO_URL", "")
GIT_USER_NAME   = "Daisy"
GIT_USER_EMAIL  = "daisy@trustedbiz.co.ug"

MAX_QUEUE_SIZE  = 50000   # cap queue so disk doesn't explode
LINKS_PER_PAGE  = 12      # how many new Wikipedia links to harvest per page

# ============================================================
# SEED TOPICS — 200 starting points across every domain
# Daisy will crawl outward from these automatically
# ============================================================
SEED_TOPICS = [
    # Science
    "Artificial_intelligence","Machine_learning","Deep_learning","Neural_network",
    "Computer_science","Algorithm","Data_structure","Programming_language",
    "Biology","Cell_(biology)","DNA","Evolution","Photosynthesis","Ecology",
    "Physics","Gravity","Electricity","Thermodynamics","Quantum_mechanics","Optics",
    "Chemistry","Atom","Molecule","Chemical_reaction","Periodic_table","Acid",
    "Mathematics","Algebra","Geometry","Calculus","Statistics","Number_theory",
    "Astronomy","Solar_system","Black_hole","Galaxy","Star","Planet",
    "Medicine","Human_body","Brain","Heart","Immune_system","Vaccine","Virus","Bacteria",
    "Psychology","Emotion","Memory","Consciousness","Behavior","Motivation",
    "Neuroscience","Nervous_system","Neuron","Synapse","Hormone",
    # Africa & Uganda
    "Uganda","Kampala","Lake_Victoria","Nile","East_Africa","African_Union",
    "History_of_Uganda","Economy_of_Uganda","Education_in_Uganda","Agriculture_in_Uganda",
    "Africa","Sub-Saharan_Africa","East_African_Community","Swahili_language","Luganda",
    "Kenya","Tanzania","Rwanda","Ethiopia","Nigeria","South_Africa","Ghana",
    "African_history","Colonialism_in_Africa","Independence_of_Africa",
    # Technology
    "Internet","World_Wide_Web","Smartphone","Cloud_computing","Cybersecurity",
    "Software_engineering","Database","Operating_system","Linux","Open_source",
    "Blockchain","Cryptocurrency","E-commerce","Digital_marketing","Social_media",
    "Robotics","Drone","3D_printing","Nanotechnology","Biotechnology",
    # Economics & Business
    "Economics","Microeconomics","Macroeconomics","Supply_and_demand","Inflation",
    "Entrepreneurship","Startup_company","Business_model","Marketing","Finance",
    "Banking","Investment","Stock_market","Trade","Globalization",
    "Poverty","Development_economics","Sustainable_development","Microfinance",
    # Society & Life
    "Education","School","University","Learning","Skill","Knowledge",
    "Democracy","Government","Law","Human_rights","Constitution","Justice",
    "Climate_change","Renewable_energy","Solar_energy","Water","Agriculture","Food",
    "Health","Nutrition","Exercise","Mental_health","Happiness","Stress",
    "Philosophy","Ethics","Logic","Critical_thinking","Epistemology",
    "Religion","Culture","Language","Communication","Leadership","Teamwork",
    # Practical skills
    "Cooking","Personal_finance","Time_management","Problem_solving",
    "Writing","Public_speaking","Negotiation","Decision-making",
    "Football","Basketball","Athletics","Sport","Olympic_Games",
    # Geography
    "Geography","Continent","Ocean","Mountain","River","Desert","Forest","Climate",
    "Population","City","Infrastructure","Transport","Energy",
]

SEED_URLS = [f"https://en.wikipedia.org/wiki/{t}" for t in SEED_TOPICS]

# ============================================================
# QUEUE & VISITED — persisted to disk
# ============================================================
def load_queue():
    try:
        if os.path.exists(QUEUE_FILE):
            with open(QUEUE_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return list(SEED_URLS)   # first run: start from seeds

def save_queue(q):
    try:
        with open(QUEUE_FILE, "w") as f:
            json.dump(q[:MAX_QUEUE_SIZE], f)
    except:
        pass

def load_visited():
    try:
        if os.path.exists(VISITED_FILE):
            with open(VISITED_FILE, "r") as f:
                return set(json.load(f))
    except:
        pass
    return set()

def save_visited(v):
    try:
        with open(VISITED_FILE, "w") as f:
            json.dump(list(v)[-20000:], f)   # keep last 20k
    except:
        pass

# In-memory state (loaded from disk at startup)
_queue   = []
_visited = set()
_queue_lock = threading.Lock()

def init_queue():
    global _queue, _visited
    _queue   = load_queue()
    _visited = load_visited()
    # Make sure seeds are in queue if queue is empty
    if not _queue:
        _queue = list(SEED_URLS)
    log(f"Queue loaded: {len(_queue)} URLs pending | {len(_visited)} visited")

# ============================================================
# STEP 1 — FETCH & CLEAN
# ============================================================
def fetch_page(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (DaisyBot/2.0; +trustedbiz.co.ug)"}
        res = requests.get(url, headers=headers, timeout=12)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup(["script","style","nav","footer","header",
                         "aside","figure","table",".navbox",".infobox",
                         ".reflist",".references",".mw-editsection"]):
            tag.decompose()
        paragraphs = soup.find_all("p")
        text = " ".join(p.get_text(" ", strip=True) for p in paragraphs)
        text = re.sub(r"\[\d+\]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text, soup
    except Exception as e:
        log(f"FETCH ERROR: {url} — {e}")
        return None, None

# ============================================================
# STEP 2 — HARVEST LINKS from page (feeds the queue)
# ============================================================
def harvest_links(soup, base="https://en.wikipedia.org"):
    if not soup:
        return []
    links = []
    for a in soup.select("#mw-content-text a[href^='/wiki/']"):
        href = a.get("href","")
        # Skip meta pages, files, categories
        if any(x in href for x in ["File:","Category:","Talk:","Special:",
                                    "Help:","Template:","Wikipedia:","Portal:",
                                    "#","(disambiguation)"]):
            continue
        full = base + href.split("#")[0]
        links.append(full)
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for l in links:
        if l not in seen:
            seen.add(l)
            unique.append(l)
    # Shuffle before capping — otherwise we always grab the same
    # heavily-linked hub pages (Science, Mathematics, etc.) that
    # appear first in every article and whose words Daisy already
    # knows. Random sampling pushes the crawl into new territory.
    random.shuffle(unique)
    return unique[:LINKS_PER_PAGE]

# ============================================================
# STEP 3 — EXTRACT RICH PAIRS
# Tries to extract definition + what_it_does + examples
# ============================================================
def extract_pairs(text, max_pairs=40):
    pairs = {}
    sentences = re.split(r"(?<=[.!?])\s+", text)

    define_pattern = re.compile(
        r"^([A-Z][a-zA-Z\s\-]{2,45})\s+"
        r"(?:is|are|refers to|means|can be defined as|was|were|involves|describes)\s+"
        r"(.{20,150})\.",
        re.IGNORECASE
    )
    does_pattern = re.compile(
        r"^(?:It|They|This|The \w+)\s+(?:can|will|may|also)?\s*"
        r"(?:help|allow|enable|provide|produce|create|generate|support|control|process)\s+"
        r"(.{15,120})\.",
        re.IGNORECASE
    )
    example_pattern = re.compile(
        r"(?:For example|Examples include|Such as|Including|e\.g\.)[,:]?\s+(.{10,100})\.",
        re.IGNORECASE
    )

    last_key = None
    for sentence in sentences:
        sentence = sentence.strip()

        # Definition match
        m = define_pattern.match(sentence)
        if m:
            raw_term = m.group(1).strip()
            definition = m.group(2).strip()
            key = re.sub(r"[^a-z0-9\s]", "", raw_term.lower())
            key = re.sub(r"\s+", "_", key).strip("_")
            if 3 <= len(key) <= 50 and key not in ("the","a","an","it","this","that","they"):
                definition = definition.rstrip(".")
                definition = definition[0].upper() + definition[1:] if definition else definition
                pairs[key] = {"definition": definition, "what_it_does": "", "examples": ""}
                last_key = key
                if len(pairs) >= max_pairs:
                    break
            continue

        # What it does — attach to last defined term
        if last_key and last_key in pairs:
            md = does_pattern.match(sentence)
            if md and not pairs[last_key]["what_it_does"]:
                pairs[last_key]["what_it_does"] = md.group(1).strip().rstrip(".")

        # Examples — attach to last defined term
        if last_key and last_key in pairs:
            me = example_pattern.search(sentence)
            if me and not pairs[last_key]["examples"]:
                pairs[last_key]["examples"] = me.group(1).strip().rstrip(".")

    return pairs

# ============================================================
# STEP 4 — READ EXISTING KEYS
# ============================================================
def get_existing_keys(jsx_path):
    try:
        with open(jsx_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Match both formats:
        #   key: "string definition",
        #   key: { definition: "...", ... },
        keys = re.findall(r'^\s{2}([a-z][a-z0-9_]+):\s*(?:"|\{)', content, re.MULTILINE)
        return set(keys)
    except Exception as e:
        log(f"READ ERROR: {e}")
        return set()

# ============================================================
# STEP 5 — WRITE INTO JSX
# ============================================================
def write_to_jsx(jsx_path, new_pairs):
    if not new_pairs:
        return 0
    try:
        with open(jsx_path, "r", encoding="utf-8") as f:
            content = f.read()
        flat_dict_start = content.find("const FLAT_DICT = {")
        if flat_dict_start == -1:
            log("ERROR: FLAT_DICT not found in JSX.")
            return 0
        search_from = flat_dict_start + len("const FLAT_DICT = {")
        closing_pos = content.find("\n};", search_from)
        if closing_pos == -1:
            log("ERROR: End of FLAT_DICT not found.")
            return 0

        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        new_lines = f"\n  // === INGESTED {timestamp} ===\n"
        for key, data in new_pairs.items():
            if isinstance(data, dict):
                definition = data.get("definition", "").replace('"', "'")
                does       = data.get("what_it_does", "").replace('"', "'")
                examples   = data.get("examples", "").replace('"', "'")
                if does or examples:
                    # Rich entry — object with all 3 fields so the
                    # synthesis engine's does()/examples() helpers work
                    new_lines += (
                        f'  {key}: {{ definition: "{definition}", '
                        f'what_it_does: "{does}", examples: "{examples}" }},\n'
                    )
                else:
                    new_lines += f'  {key}: "{definition}",\n'
            else:
                safe_def = str(data).replace('"', "'")
                new_lines += f'  {key}: "{safe_def}",\n'

        new_content = content[:closing_pos] + new_lines + content[closing_pos:]
        with open(jsx_path, "w", encoding="utf-8") as f:
            f.write(new_content)
        log(f"WROTE {len(new_pairs)} new entries into {jsx_path}")
        return len(new_pairs)
    except Exception as e:
        log(f"WRITE ERROR: {e}")
        return 0

# ============================================================
# STEP 6 — FILTER NEW ONLY
# ============================================================
def filter_new_only(pairs, existing_keys):
    return {k: v for k, v in pairs.items() if k not in existing_keys}

# ============================================================
# GIT AUTO-PUSH
# ============================================================
def git_push(word_count):
    if not GITHUB_TOKEN or not GITHUB_REPO_URL:
        log("GIT PUSH SKIPPED: tokens not set.")
        return
    try:
        repo_dir = os.path.dirname(os.path.abspath(JSX_FILE_PATH)) or "."
        if "https://" in GITHUB_REPO_URL:
            auth_url = GITHUB_REPO_URL.replace("https://", f"https://{GITHUB_TOKEN}@")
        else:
            auth_url = GITHUB_REPO_URL
        env = {
            **os.environ,
            "GIT_AUTHOR_NAME": GIT_USER_NAME,
            "GIT_AUTHOR_EMAIL": GIT_USER_EMAIL,
            "GIT_COMMITTER_NAME": GIT_USER_NAME,
            "GIT_COMMITTER_EMAIL": GIT_USER_EMAIL,
        }
        def run(cmd):
            r = subprocess.run(cmd, cwd=repo_dir, env=env, capture_output=True, text=True)
            return r.returncode, r.stdout.strip(), r.stderr.strip()

        # Stage both JSX and queue files
        run(["git", "add", JSX_FILE_PATH])
        run(["git", "add", QUEUE_FILE])
        run(["git", "add", VISITED_FILE])

        code, _, _ = run(["git", "diff", "--cached", "--quiet"])
        if code == 0:
            log("GIT: Nothing new to commit.")
            return

        msg = f"Daisy learned {word_count} new words [{datetime.now().strftime('%Y-%m-%d %H:%M')}]"
        code, _, err = run(["git", "commit", "-m", msg])
        if code != 0:
            log(f"GIT COMMIT failed: {err}")
            return

        # Detect current branch — Render often has detached HEAD
        code, branch, _ = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
        if branch == "HEAD" or not branch:
            # Detached HEAD — create/checkout main branch from current commit
            run(["git", "checkout", "-b", "main"])
            branch = "main"

        code, _, err = run(["git", "push", auth_url, f"HEAD:{branch}"])
        if code != 0:
            # Last resort — force push to main
            code, _, err = run(["git", "push", auth_url, "HEAD:main", "--force"])
        if code == 0:
            log(f"GIT PUSH SUCCESS — {word_count} words saved to GitHub.")
        else:
            log(f"GIT PUSH failed: {err}")
    except Exception as e:
        log(f"GIT PUSH ERROR: {e}")

# ============================================================
# MAIN INGEST CYCLE
# ============================================================
def ingest_one(url=None):
    global _queue, _visited

    # Pick next URL from queue
    if not url:
        with _queue_lock:
            # Remove already-visited from front of queue
            while _queue and _queue[0] in _visited:
                _queue.pop(0)
            if not _queue:
                # Queue empty — refill from seeds
                log("Queue empty — refilling from seeds.")
                _queue = [u for u in SEED_URLS if u not in _visited]
                if not _queue:
                    # Visited everything — reset visited and start over
                    log("All seeds visited — resetting visited set for new cycle.")
                    _visited = set()
                    _queue = list(SEED_URLS)
            url = _queue.pop(0)

    if url in _visited:
        log(f"SKIP (already visited): {url}")
        return

    log(f"--- INGESTING: {url}")
    _visited.add(url)

    # Fetch
    text, soup = fetch_page(url)
    if not text:
        log("SKIP: Empty page.")
        return

    # Harvest new links and add to queue
    new_links = harvest_links(soup)
    with _queue_lock:
        added_links = 0
        for link in new_links:
            if link not in _visited and link not in _queue:
                _queue.append(link)
                added_links += 1
        log(f"QUEUE: +{added_links} new URLs discovered | Queue size: {len(_queue)}")

    # Extract
    pairs = extract_pairs(text, max_pairs=40)
    log(f"EXTRACTED: {len(pairs)} raw pairs")

    # Filter
    existing = get_existing_keys(JSX_FILE_PATH)
    new_pairs = filter_new_only(pairs, existing)
    log(f"NEW (not in Daisy yet): {len(new_pairs)} pairs")

    if not new_pairs:
        log("Daisy already knows all of these. Moving on.")
        # Still save queue progress
        save_queue(_queue)
        save_visited(_visited)
        return

    # Write
    written = write_to_jsx(JSX_FILE_PATH, new_pairs)
    log(f"SUCCESS: {written} words added. Daisy total: ~{len(existing) + written}")

    # Persist queue and visited
    save_queue(_queue)
    save_visited(_visited)

    # Push to GitHub
    git_push(written)

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
# FLASK INTEGRATION
# ============================================================
daisy_bp = Blueprint("daisy", __name__)

_ingest_thread = None
_last_ingest   = None
_ingest_count  = 0
_running       = False


def _background_scheduler(interval_minutes):
    global _last_ingest, _ingest_count, _running
    _running = True
    init_queue()
    log(f"=== DAISY CRAWLER STARTED (every {interval_minutes}m) ===")
    while _running:
        try:
            ingest_one()
            _last_ingest = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            _ingest_count += 1
        except Exception as e:
            log(f"SCHEDULER ERROR: {e}")
        for _ in range(interval_minutes * 60):
            if not _running:
                break
            time.sleep(1)


def init_daisy(app, interval_minutes=2):
    global _ingest_thread
    if not os.path.exists(JSX_FILE_PATH):
        print(f"[DAISY] WARNING: JSX file not found at '{JSX_FILE_PATH}'.")
        return
    _ingest_thread = threading.Thread(
        target=_background_scheduler,
        args=(interval_minutes,),
        daemon=True
    )
    _ingest_thread.start()
    print(f"[DAISY] Crawler running — every {interval_minutes} minute(s)")


@daisy_bp.route("/daisy/status", methods=["GET"])
def daisy_status():
    existing = get_existing_keys(JSX_FILE_PATH)
    log_tail = []
    try:
        if os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, "r") as f:
                log_tail = [l.strip() for l in f.readlines()[-10:]]
    except:
        pass
    return jsonify({
        "status": "running" if _running else "stopped",
        "words": len(existing),
        "ingest_cycles": _ingest_count,
        "last_ingest": _last_ingest or "not yet",
        "queue_size": len(_queue),
        "visited_count": len(_visited),
        "log_tail": log_tail
    })


@daisy_bp.route("/daisy/ingest", methods=["POST"])
def daisy_ingest_now():
    url = request.args.get("url", None)
    try:
        ingest_one(url=url)
        existing = get_existing_keys(JSX_FILE_PATH)
        return jsonify({
            "success": True,
            "words": len(existing),
            "queue_size": len(_queue),
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


# ============================================================
# ENTRY POINT (standalone)
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daisy Ingestion Engine v2")
    parser.add_argument("--url", type=str, help="Ingest one specific URL")
    parser.add_argument("--auto", action="store_true", help="Run crawler continuously")
    parser.add_argument("--interval", type=int, default=1, help="Minutes between cycles")
    args = parser.parse_args()

    if not os.path.exists(JSX_FILE_PATH):
        print(f"ERROR: JSX file not found at '{JSX_FILE_PATH}'")
        exit(1)

    init_queue()

    if args.url:
        ingest_one(url=args.url)
    elif args.auto:
        import schedule
        log(f"=== DAISY CRAWLER STARTED — every {args.interval}m ===")
        ingest_one()
        schedule.every(args.interval).minutes.do(ingest_one)
        while True:
            schedule.run_pending()
            time.sleep(10)
    else:
        ingest_one()
