"""
DAISY HARVEST — data engine for Petal (Daisy's own model)
==========================================================
Collects clean text for training a model from scratch.

Outputs (all plain files, same spirit as the rest of Daisy):
  petal_corpus.txt     raw clean prose, one paragraph per line   -> pretraining
  petal_pairs.jsonl    {"instruction","response"} pairs          -> style / Q&A tuning
  petal_sources.jsonl  where every page came from                -> attribution
  petal_queue.json     crawl queue (survives restarts)
  petal_visited.json   pages already harvested

Usage:
  python daisy_harvest.py crawl --pages 100     # harvest Wikipedia pages
  python daisy_harvest.py export                # corrections + conversations -> pairs
  python daisy_harvest.py stats                 # how much data do we have?

Wikipedia text is CC BY-SA: keep petal_sources.jsonl and credit Wikipedia.
"""

import argparse
import hashlib
import json
import os
import random
import re
import time
from pathlib import Path

PETAL_NAME = "Petal"

BASE = Path(__file__).parent
# Everything Petal learns and produces lives in DATA (default: next to this file).
# GitHub Actions points PETAL_DATA_DIR at the checked-out petal-data branch.
DATA = Path(os.environ.get("PETAL_DATA_DIR") or BASE)
DATA.mkdir(parents=True, exist_ok=True)
CORPUS_FILE = DATA / "petal_corpus.txt"
PAIRS_FILE = DATA / "petal_pairs.jsonl"
SOURCES_FILE = DATA / "petal_sources.jsonl"
QUEUE_FILE = DATA / "petal_queue.json"
VISITED_FILE = DATA / "petal_visited.json"
MAX_CORPUS_BYTES = int(float(os.environ.get("PETAL_MAX_CORPUS_MB", "150")) * 1_000_000)
USE_CONVERSATIONS = os.environ.get("PETAL_USE_CONVERSATIONS") == "1"   # off by default: privacy

# Existing Daisy files we reuse as seeds / training data
DAISY_QUEUE = BASE / "daisy_queue.json"
CORRECTIONS_FILE = BASE / "daisy_corrections.json"
CONVERSATIONS_FILE = BASE / "daisy_conversations.jsonl"

HEADERS = {"User-Agent": f"{PETAL_NAME}Harvester/1.0 (Daisy project; polite crawler)"}
DELAY_SECONDS = 1.0          # be polite
MIN_PARAGRAPH_CHARS = 120    # skip tiny fragments
MAX_PARAGRAPH_CHARS = 1500
MIN_PAGE_PARAGRAPHS = 3      # skip stubs
LINKS_PER_PAGE = 12
MAX_QUEUE = 50000

SEEDS = [
    "Science", "Physics", "Chemistry", "Biology", "Mathematics", "Astronomy",
    "Medicine", "Geography", "History", "Economics", "Computer_science",
    "Africa", "Uganda", "Nigeria", "Language", "Literature", "Philosophy",
]


# ---------------------------------------------------------------- helpers
def _load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _hash(text):
    norm = re.sub(r"\W+", " ", text.lower()).strip()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- cleaning
_CITE = re.compile(r"\[\s*(?:\d+|[a-z]|citation needed|note \d+)\s*\]", re.I)
_PARENS_EMPTY = re.compile(r"\(\s*[;,]?\s*\)")
_SPACES = re.compile(r"[ \t\u00a0]+")


def clean_paragraph(text):
    text = _CITE.sub("", text)
    text = _PARENS_EMPTY.sub("", text)
    text = _SPACES.sub(" ", text).strip()
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    return text


def good_paragraph(text):
    if not (MIN_PARAGRAPH_CHARS <= len(text) <= MAX_PARAGRAPH_CHARS):
        return False
    letters = sum(c.isalpha() for c in text)
    if letters / len(text) < 0.7:          # tables, coordinates, junk
        return False
    if not text.rstrip().endswith((".", "!", "?", '"', ")")):
        return False
    return True


def parse_page(html, base="https://en.wikipedia.org"):
    """Returns (title, [clean paragraphs], [links])."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    h1 = soup.find("h1")
    title = h1.get_text(" ", strip=True) if h1 else ""
    body = soup.select_one("#mw-content-text") or soup
    for bad in body.select("sup, table, style, script, .reference, .mw-editsection, .hatnote, .navbox"):
        bad.decompose()

    paras = []
    for p in body.find_all("p"):
        t = clean_paragraph(p.get_text(" ", strip=True))
        if good_paragraph(t):
            paras.append(t)

    links = []
    for a in body.find_all("a", href=True):
        h = a["href"]
        if h.startswith("/wiki/") and ":" not in h and "#" not in h:
            links.append(base + h)
    return title, paras, links


# ---------------------------------------------------------------- templates
def make_pairs(title, paras):
    """Cheap, honest question/answer pairs from a page. Templates, not magic."""
    if not title or not paras:
        return []
    first = paras[0]
    pairs = [
        {"instruction": f"What is {title}?", "response": first},
        {"instruction": f"Explain {title}.", "response": " ".join(paras[:2])},
    ]
    if len(paras) >= 3:
        pairs.append({"instruction": f"Tell me more about {title}.", "response": paras[2]})
    return pairs


# ---------------------------------------------------------------- crawl
def _fetch(url):
    import requests

    r = requests.get(url, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.text


def _init_queue():
    queue = _load_json(QUEUE_FILE, None)
    if queue is not None:
        return queue
    queue = [f"https://en.wikipedia.org/wiki/{s}" for s in SEEDS]
    queue += _load_json(DAISY_QUEUE, [])[:5000]     # reuse Daisy's discovered URLs
    random.shuffle(queue)
    return queue


def harvest_page(url, seen_hashes):
    """Fetch one page. Returns (paragraphs_written, links)."""
    title, paras, links = parse_page(_fetch(url))
    if len(paras) < MIN_PAGE_PARAGRAPHS:
        return 0, links

    fresh = []
    for p in paras:
        h = _hash(p)
        if h not in seen_hashes:
            seen_hashes.add(h)
            fresh.append(p)
    if not fresh:
        return 0, links

    with open(CORPUS_FILE, "a", encoding="utf-8") as f:
        for p in fresh:
            f.write(p + "\n")
    with open(PAIRS_FILE, "a", encoding="utf-8") as f:
        for pair in make_pairs(title, fresh):
            f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    with open(SOURCES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"url": url, "title": title, "license": "CC BY-SA"}) + "\n")
    return len(fresh), links


def _rebuild_hashes():
    seen = set()
    if CORPUS_FILE.exists():
        with open(CORPUS_FILE, encoding="utf-8") as f:
            for line in f:
                seen.add(_hash(line))
    return seen


def crawl(pages, max_seconds=None):
    queue = _init_queue()
    visited = set(_load_json(VISITED_FILE, []))
    seen_hashes = _rebuild_hashes()
    done = total = fails = 0
    started = time.time()

    while queue and done < pages:
        if max_seconds and time.time() - started > max_seconds:
            print("Time budget reached for harvesting.")
            break
        if CORPUS_FILE.exists() and CORPUS_FILE.stat().st_size >= MAX_CORPUS_BYTES:
            print(f"Corpus reached its cap ({MAX_CORPUS_BYTES/1e6:.0f} MB). Nothing more to harvest.")
            break
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            n, links = harvest_page(url, seen_hashes)
        except Exception as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if not (code and 400 <= code < 500):       # network trouble: try this page again later
                visited.discard(url)
                if len(queue) < MAX_QUEUE:
                    queue.append(url)
            print(f"[skip] {url}: {str(e)[:120]}")
            fails += 1
            if fails >= 5:
                print("Too many failures in a row (offline?). Stopping this round.")
                break
            continue
        fails = 0
        done += 1
        total += n
        random.shuffle(links)
        for l in links[:LINKS_PER_PAGE]:
            if l not in visited and len(queue) < MAX_QUEUE:
                queue.append(l)
        print(f"[{done}/{pages}] +{n} paragraphs  {url}")
        _save_json(QUEUE_FILE, queue)
        _save_json(VISITED_FILE, sorted(visited))
        time.sleep(DELAY_SECONDS)
    print(f"Done. {total} new paragraphs from {done} pages.")


# ---------------------------------------------------------------- export own data
def export_daisy_data():
    """Corrections you taught + real conversations -> training pairs (Daisy's own voice)."""
    pairs = []
    for q, a in _load_json(CORRECTIONS_FILE, {}).items():
        if q and a and len(a) > 15:
            pairs.append({"instruction": q, "response": a})

    if USE_CONVERSATIONS and CONVERSATIONS_FILE.exists():
        for line in CONVERSATIONS_FILE.read_text(encoding="utf-8").splitlines():
            try:
                ex = json.loads(line)
            except Exception:
                continue
            # only keep answers Daisy actually knew, not her "I don't know" fallbacks
            if ex.get("daisy") and ex.get("source") not in ("unknown", None):
                pairs.append({"instruction": ex["user"], "response": ex["daisy"]})

    seen, out = set(), []
    for p in pairs:
        h = _hash(p["instruction"] + p["response"])
        if h not in seen:
            seen.add(h)
            out.append(p)

    path = DATA / "petal_own_pairs.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for p in out:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")
    print(f"Exported {len(out)} pairs -> {path.name}")
    if not USE_CONVERSATIONS:
        print("(Real user conversations are NOT used. Set PETAL_USE_CONVERSATIONS=1 to include them; a public Petal could repeat what users typed.)")


# ---------------------------------------------------------------- stats
def stats():
    def count(p):
        return sum(1 for _ in open(p, encoding="utf-8")) if Path(p).exists() else 0

    paras = count(CORPUS_FILE)
    chars = CORPUS_FILE.stat().st_size if CORPUS_FILE.exists() else 0
    print(f"{PETAL_NAME} data")
    print(f"  corpus paragraphs : {paras:,}")
    print(f"  corpus size       : {chars/1e6:.2f} MB (~{chars//4:,} tokens)")
    print(f"  q/a pairs         : {count(PAIRS_FILE):,}")
    print(f"  pages harvested   : {count(SOURCES_FILE):,}")
    print("  rough guide: 10 MB = toy, 100 MB = babbles sensibly, 1 GB+ = starts to feel real")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=f"{PETAL_NAME} data harvester")
    ap.add_argument("cmd", choices=["crawl", "export", "stats"])
    ap.add_argument("--pages", type=int, default=50)
    args = ap.parse_args()
    {"crawl": lambda: crawl(args.pages), "export": export_daisy_data, "stats": stats}[args.cmd]()
