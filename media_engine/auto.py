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
