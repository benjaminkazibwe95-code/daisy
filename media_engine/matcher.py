"""Finds the best library item for a query. Below THRESHOLD = a miss (no card)."""
import re
from datetime import date
from . import db

THRESHOLD = 0.4
STOP = {"how","do","i","to","a","the","my","me","can","what","is","of","for","in","on","and","with","show","get"}

def _stem(w: str) -> str:
    return w[:6]  # crude: register/registration -> regist. Swap for a real stemmer later.

def tokens(s: str) -> set:
    return {_stem(w) for w in re.findall(r"\w+", (s or "").lower()) if w not in STOP}

# Words that appear in almost every question here and say nothing about WHAT is wanted.
WEAK = {"uganda", "ugandan", "please", "pls", "hey", "hello", "daisy", "tell", "need", "want", "know", "about", "help",
        "some", "any", "there", "would", "could", "should", "also", "just"}

def score(query: str, item, lang: str) -> float:
    q = {t for t in tokens(query) if t not in {_stem(w) for w in WEAK}}
    if not q:
        return 0.0
    tags, title = tokens(item["tags"]), tokens(item["title"])
    hit = q & (tags | title)
    denom = min(len(q), 6)                       # long questions must not dilute the score
    # RELEVANCE GATE: bonuses below must never rescue a weak match. "register my business" must not pull
    # "Architects Registration Board" (one shared word) or a podcast that only says "business".
    if len(hit) < min(2, len(q)) or len(hit) / denom < 0.5:
        return 0.0
    s = len(hit) / denom
    s += 0.15 * len(q & tags) / denom          # tag hits count more
    if item["lang"] == lang:
        s += 0.1                                 # prefer the user's language
    if item["tier"] in ("official", "curated"):
        s += 0.1                                 # official sources win ties
    seen = item["helpful"] + item["unhelpful"]
    if seen >= 5:                                # feedback tunes ranking
        s += 0.2 * (item["helpful"] / seen - 0.5)
    return min(s, 1.0)

def find(query: str, kind: str, lang: str = "en", exclude=(), statuses=("active",), tiers=None):
    today = date.today().isoformat()
    marks = ",".join("?" * len(statuses))
    with db.conn() as c:
        rows = c.execute(
            f"SELECT * FROM items WHERE kind=? AND status IN ({marks}) "
            "AND (review_by IS NULL OR review_by>=?)", (kind, *statuses, today)).fetchall()
    best, best_s = None, 0.0
    for r in rows:
        if r["id"] in exclude or (tiers and r["tier"] not in tiers):
            continue
        s = score(query, r, lang)
        if s > best_s:
            best, best_s = r, s
    return (best, best_s) if best_s >= THRESHOLD else (None, best_s)
