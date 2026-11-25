"""Question -> media cards, with no help from the language model.
Daisy's answer streams exactly as before; this runs after it, so an answer is never delayed or changed.
Privacy: only a few keywords are matched and logged, never the whole question, and never who asked."""
import asyncio, re
from . import matcher, resolver

NEEDS = re.compile(r"\b(how|steps?|guide|register|registration|apply|application|where|find|locate|office|address|"
                   r"requirements?|process|procedure|documents?|show|video|picture|image|photo|map|tutorial|learn|"
                   r"directions?|located)\b", re.I)
PLACE = re.compile(r"\b(where|office|address|location|located|map|directions?)\b", re.I)

def keywords(question: str, n: int = 6) -> str:
    seen, out = set(), []
    for w in re.findall(r"[a-z]{3,}", question.lower()):
        if w not in matcher.STOP and w not in seen:
            seen.add(w)
            out.append(w)
    return " ".join(out[:n])

def _run(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures as cf          # an event loop already runs in this thread
    with cf.ThreadPoolExecutor(1) as ex:
        return ex.submit(asyncio.run, coro).result()

def build_blocks(question: str):
    q = keywords(question)
    if len(q.split()) < 2 or not NEEDS.search(question):
        return []                             # only when media clearly helps
    blocks = [{"type": "media", "kind": "video", "query": q},
              {"type": "media", "kind": "image", "query": q},
              {"type": "media", "kind": "image", "query": q},
              {"type": "link", "kind": "tiktok", "query": q}]
    if PLACE.search(question):
        blocks.append({"type": "media", "kind": "map", "query": q})
    return blocks

def clean_marks(marks, n: int = 3):
    """Tags written by Daisy: keep only known kinds and a few plain search words."""
    out = []
    for m in (marks or [])[:n]:
        if not isinstance(m, dict) or m.get("kind") not in ("video", "image", "map"):
            continue
        q = " ".join(re.findall(r"[\w']+", str(m.get("query", "")))[:8])[:80]
        if q:
            out.append({"kind": m["kind"], "query": q})
    return out

def media_for_marks(marks, mode: str = "thumbs", lang: str = "en"):
    """List aligned with the cleaned marks: a card or None for each. Never raises, never takes long."""
    marks = clean_marks(marks)
    if mode == "text" or not marks:
        return [None] * len(marks)
    try:
        return _run(asyncio.wait_for(resolver.resolve_marks(marks, mode, lang), 8))
    except Exception:
        return [None] * len(marks)

def media_for(question: str, mode: str = "thumbs", lang: str = "en"):
    if mode == "text":
        return []
    blocks = build_blocks(question)
    if not blocks:
        return []
    try:
        return _run(asyncio.wait_for(resolver.resolve(blocks, mode, lang, question), 6))
    except Exception:
        return []                             # media must never break or delay Daisy
