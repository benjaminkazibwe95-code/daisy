"""Automatic trust scoring for community (non-official) videos. No human needed.
Hard gates first, then a 0-1 score. Official channels skip this entirely."""
import math, re
from datetime import date, datetime
from . import matcher

MIN_SCORE = 0.65
RED_FLAGS = ["guaranteed", "easy money", "get rich", "100% approved", "send money", "pay me",
             "whatsapp me", "no documents needed", "bypass", "fake", "loophole", "cheat",
             "double your money", "forex signal"]
GENERIC = matcher.STOP | {"video", "official", "watch", "subscribe", "channel", "like", "share",
                          "comment", "https", "http", "www", "com", "please", "also", "this", "that", "from", "your", "will"}

def age_years(published: str) -> float:
    try:
        d = datetime.fromisoformat(published.replace("Z", "+00:00")).date()
    except Exception:
        return 99.0
    return (date.today() - d).days / 365

def red_flag(text: str):
    t = text.lower()
    return next((f for f in RED_FLAGS if f in t), None)

def community_score(query: str, v: dict, subs: int):
    """v: title, description, views, likes (or None), published. Returns (score, reason)."""
    text = f"{v['title']} {v['description'][:500]}"
    if red_flag(text):
        return 0.0, "red-flag wording"
    q = matcher.tokens(query)
    if not q:
        return 0.0, "empty query"
    title_rel = len(q & matcher.tokens(v["title"])) / len(q)
    body_rel = len(q & matcher.tokens(text)) / len(q)
    if title_rel < 0.5:
        return 0.0, "title not relevant"
    views = v["views"]
    if views < 1000:
        return 0.0, "too few views"
    if 0 < subs < 500:
        return 0.0, "tiny channel"
    age = age_years(v["published"])
    if age > 4:
        return 0.0, "too old"
    likes = v.get("likes")
    like_s = 0.5 if likes is None else min((likes / views) / 0.04, 1.0)   # 4% like rate = full marks
    score = (0.40 * title_rel + 0.10 * body_rel
             + 0.20 * min(math.log10(views) / 6, 1)
             + 0.10 * like_s
             + 0.10 * min(math.log10(max(subs, 1)) / 5, 1)
             + 0.10 * (1.0 if age < 1 else 0.5 if age < 3 else 0.2))
    return round(score, 3), ("ok" if score >= MIN_SCORE else "below threshold")

def auto_tags(title: str, description: str, n: int = 12) -> str:
    """Tags from what is written: title words count triple."""
    freq = {}
    for text, weight in ((title, 3), (description[:600], 1)):
        for w in re.findall(r"[a-z]{4,}", text.lower()):
            if w not in GENERIC:
                freq[w] = freq.get(w, 0) + weight
    return " ".join(w for w, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:n])
