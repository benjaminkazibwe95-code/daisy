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

What she reads (picked by weight each time, so no single source crowds out the rest):
  Wikipedia                    articles               CC BY-SA
  Simple English Wikipedia     plainer sentences      CC BY-SA
  Wikibooks                    textbooks, how-tos     CC BY-SA
  Project Gutenberg books      people talking         public domain
CC BY-SA text needs credit: keep petal_sources.jsonl and credit the sources on the website.
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
WIKIBOOKS_SEEDS = [
    "Wikibooks:Featured_books", "Chemistry", "Algebra", "Biology", "Physics_Study_Guide",
    "Cookbook:Table_of_Contents", "Chess", "Statistics", "Geography", "World_History",
]

# Public-domain books from Project Gutenberg (people talking, stories, plain English).
# A wrong id just gives a 404 and is skipped.
GUTENBERG_BOOKS = [
    11, 12, 16, 35, 36, 43, 46, 55, 74, 76, 84, 98, 120, 158, 161, 174, 205, 215, 244, 345,
    514, 521, 768, 844, 1260, 1342, 1400, 1661, 2097, 2591, 2701, 1184, 1232, 3207, 2814, 730, 829, 1952, 2554, 4300,
]
BOOK_MAX_BYTES = int(float(os.environ.get("PETAL_BOOK_MAX_KB", "300")) * 1000)   # per book, keeps one novel from filling the corpus
BOOK_CHUNK_MIN, BOOK_CHUNK_MAX = 400, 1200      # consecutive paragraphs are joined so dialogue stays together

# How often each source is chosen (when it has something waiting in the queue)
SOURCE_WEIGHTS = {"wiki": 45, "simple": 25, "wikibooks": 10, "books": 20}
LICENSES = {"wiki": "CC BY-SA", "simple": "CC BY-SA", "wikibooks": "CC BY-SA", "books": "Public domain (Project Gutenberg)"}


def gutenberg_url(book_id):
    return f"https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt"


def source_group(url):
    if "gutenberg.org" in url:
        return "books"
    if "simple.wikipedia.org" in url:
        return "simple"
    if "wikibooks.org" in url:
        return "wikibooks"
    return "wiki"


def all_seeds():
    return ([f"https://en.wikipedia.org/wiki/{s}" for s in SEEDS]
            + [f"https://simple.wikipedia.org/wiki/{s}" for s in SEEDS]
            + [f"https://en.wikibooks.org/wiki/{s}" for s in WIKIBOOKS_SEEDS]
            + [gutenberg_url(b) for b in GUTENBERG_BOOKS])


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


def parse_book(text):
    """Project Gutenberg plain text -> chunks of consecutive paragraphs (dialogue stays together)."""
    m = re.search(r"\*\*\* ?START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK.*?\*\*\*", text, re.I | re.S)
    if m:
        text = text[m.end():]
    e = re.search(r"\*\*\* ?END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK", text, re.I)
    if e:
        text = text[:e.start()]
    text = text.replace("\r\n", "\n")
    text = text[int(len(text) * 0.04):]                    # skip contents pages / prefaces at the front

    paras = []
    for raw in re.split(r"\n\s*\n", text):
        t = clean_paragraph(raw.replace("_", ""))
        if len(t) < 40 or t.isupper():                     # headings, page numbers
            continue
        if sum(c.isalpha() for c in t) / len(t) < 0.7:
            continue
        if not t.rstrip().endswith((".", "!", "?", '"', ")", "\u201d", "\u2019")):
            continue
        paras.append(t)

    chunks, cur, total = [], "", 0
    for t in paras:
        if cur and len(cur) + 1 + len(t) > BOOK_CHUNK_MAX:
            chunks.append(cur)
            total += len(cur)
            cur = ""
            if total >= BOOK_MAX_BYTES:
                break
        cur = (cur + " " + t).strip()
        if len(cur) >= BOOK_CHUNK_MIN:
            chunks.append(cur)
            total += len(cur)
            cur = ""
            if total >= BOOK_MAX_BYTES:
                break
    return [c for c in chunks if len(c) >= 80]


# ---------------------------------------------------------------- templates
def make_pairs(title, paras):
    """Cheap, honest question/answer pairs from a page. Templates, not magic."""
    if not title or not paras:
        return []
    first = paras[0]
    # (No "Explain X" pair that glues paragraphs together: it only repeats text the corpus
    # already holds, and it let held-out test text leak into training.)
    pairs = [{"instruction": f"What is {title}?", "response": first}]
    if len(paras) >= 3:
        pairs.append({"instruction": f"Tell me more about {title}.", "response": paras[2]})
    return pairs


# ---------------------------------------------------------------- crawl
def _fetch(url):
    import requests

    r = requests.get(url, headers=HEADERS, timeout=30 if "gutenberg.org" in url else 15)
    r.raise_for_status()
    if "gutenberg.org" in url:
        r.encoding = "utf-8"
    return r.text


def _init_queue():
    queue = _load_json(QUEUE_FILE, None)
    if queue is not None:
        return queue
    queue = [f"https://en.wikipedia.org/wiki/{s}" for s in SEEDS]
    queue += _load_json(DAISY_QUEUE, [])[:5000]     # reuse Daisy's discovered URLs
    random.shuffle(queue)
    return queue


def _ensure_seeds(queue, visited):
    """Adds the starting pages of every source that are not read and not waiting yet.
    Runs every time, so an existing Petal automatically starts reading the new sources."""
    waiting = set(queue)
    added = 0
    for url in reversed(all_seeds()):
        if url not in visited and url not in waiting:
            queue.insert(0, url)
            added += 1
    return added


def _pick(queue):
    """Takes the next URL, choosing a source by weight (falls back to the front of the queue)."""
    groups = list(SOURCE_WEIGHTS)
    g = random.choices(groups, weights=[SOURCE_WEIGHTS[x] for x in groups])[0]
    for i, u in enumerate(queue):
        if source_group(u) == g:
            return queue.pop(i)
    return queue.pop(0)


def harvest_page(url, seen_hashes):
    """Fetch one page or book. Returns (paragraphs_written, links)."""
    group = source_group(url)
    if group == "books":
        title = f"Gutenberg #{url.rsplit('/', 1)[-1].replace('pg', '').replace('.txt', '')}"
        paras, links = parse_book(_fetch(url)), []
        min_paras = 1
    else:
        from urllib.parse import urlparse
        u = urlparse(url)
        title, paras, links = parse_page(_fetch(url), base=f"{u.scheme}://{u.netloc}")
        min_paras = MIN_PAGE_PARAGRAPHS
    if len(paras) < min_paras:
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
    if group in ("wiki", "simple"):                       # question/answer templates only make sense for articles
        with open(PAIRS_FILE, "a", encoding="utf-8") as f:
            for pair in make_pairs(title, fresh):
                f.write(json.dumps(pair, ensure_ascii=False) + "\n")
    with open(SOURCES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"url": url, "title": title, "license": LICENSES[group]}) + "\n")
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
    added = _ensure_seeds(queue, visited)
    if added:
        print(f"Added {added} starting pages from new sources (Simple Wikipedia, Wikibooks, books).")
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
        url = _pick(queue)
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
            if code and 400 <= code < 500:          # a missing page is not a network problem
                continue
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
