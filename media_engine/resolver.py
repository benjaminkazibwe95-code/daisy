"""Turns Daisy's blocks into final UI blocks: picks media, enforces limits, applies data mode."""
import asyncio, json, os, re, time
from collections import Counter
from . import db, matcher, fallback

IMAGES_ON = os.getenv("MEDIA_IMAGES", "1") == "1"      # MEDIA_IMAGES=0 on Render switches pictures off everywhere
LIMITS = {"video": 1, "image": 2, "map": 1}
LIMITS_INLINE = {"video": 1, "image": 2, "map": 1}      # cards placed inside an answer, one per tag Daisy wrote
HOWTO = re.compile(r"\b(how|steps?|guide|register|apply|start|open|get)\b", re.I)

def _remote(meta):
    u = meta.get("remote") or meta.get("remote_thumb")
    return u if isinstance(u, str) and u.startswith("https://") else None

def _popup(item, meta):
    if item["kind"] == "video":
        return {"player": "youtube", "embed_url": meta.get("embed_url")}
    if item["kind"] == "image":
        return {"image": f"/media/{meta['full']}" if meta.get("full") else _remote(meta),
                "source_url": item["source_url"]}
    if item["kind"] == "map":
        return {"lat": meta.get("lat"), "lng": meta.get("lng"),
                "zoom": meta.get("zoom", 15), "label": meta.get("label", item["title"])}
    return {}

def _card(item, mode):
    meta = json.loads(item["meta"] or "{}")
    card = {
        "type": "card", "id": item["id"], "kind": item["kind"],
        "title": item["title"], "credit": item["credit"],
        "thumb": f"/media/{item['thumb']}" if item["thumb"] else _remote(meta),
        "popup": _popup(item, meta),
    }
    if mode == "full":
        card["preload"] = True
    card["tier"] = item["tier"]
    card["label"] = {"official": "Official", "community": "Community video"}.get(item["tier"])
    if item["status"] == "pending":
        card["unverified"] = True        # live result, not yet reviewed by you
    return card

TIMEOUT = {"video": 7, "image": 5, "map": 4}       # seconds each; a slow one never holds back the others

async def _one(kind, q, lang):
    # pictures inside an answer must look like real photos or diagrams: never a crawled logo / page image
    t0 = time.time()
    item, _ = matcher.find(q, kind, lang, tiers=(("auto", "curated") if kind == "image" else None))
    if item:
        print(f"[media] {kind} '{q[:60]}' -> library {time.time()-t0:.1f}s", flush=True)
        return item
    db.log_miss(q, kind)
    try:
        item = await asyncio.wait_for(fallback.find_pending_or_live(q, kind, lang), TIMEOUT.get(kind, 5))
    except Exception as e:
        print(f"[media] {kind} '{q[:60]}' -> TIMED OUT / failed ({type(e).__name__}) {time.time()-t0:.1f}s", flush=True)
        return None
    print(f"[media] {kind} '{q[:60]}' -> {'live' if item else 'nothing found'} {time.time()-t0:.1f}s", flush=True)
    return item

async def resolve_marks(marks, data_mode="thumbs", lang="en"):
    """One card (or None) per tag Daisy wrote, in the SAME order. All tags are searched AT THE SAME TIME
    (library first, then a live search), each with its own time limit, so the slowest one sets the wait."""
    used, plan = Counter(), []
    for m in marks:
        kind, q = m.get("kind"), (m.get("query") or "").strip()
        ok = data_mode != "text" and kind in LIMITS_INLINE and used[kind] < LIMITS_INLINE[kind] and q and (IMAGES_ON or kind != "image")
        if ok:
            used[kind] += 1
        plan.append((kind, q) if ok else None)

    async def run(p):
        return await _one(p[0], p[1], lang) if p else None

    items = await asyncio.gather(*[run(p) for p in plan], return_exceptions=True)
    out, seen = [], set()
    for it in items:
        if it is None or isinstance(it, Exception) or it["id"] in seen:
            out.append(None)
            continue
        seen.add(it["id"])
        out.append(_card(it, data_mode))
    return out

async def resolve(blocks, data_mode="thumbs", lang="en", question=""):
    """data_mode: text | thumbs | full"""
    used, used_ids, out = Counter(), set(), []

    async def media(spec):
        if data_mode == "text":
            return None
        kind = spec.get("kind")
        if used[kind] >= LIMITS.get(kind, 0):
            return None
        item, _ = matcher.find(spec.get("query", ""), kind, lang, exclude=used_ids)
        if not item:
            db.log_miss(spec.get("query", ""), kind)
            item = await fallback.find_pending_or_live(spec.get("query", ""), kind, lang)
            if not item:
                return None
        used[kind] += 1
        used_ids.add(item["id"])
        return _card(item, data_mode)

    for b in blocks:
        t = b.get("type")
        if t == "text":
            out.append(b)
        elif t == "media":
            card = await media(b)
            if card:
                out.append(card)
        elif t == "steps":
            steps = []
            for s in b.get("items", []):
                step = {"text": s["text"]}
                if s.get("media"):
                    card = await media(s["media"])
                    if card:
                        step["media"] = card
                steps.append(step)
            out.append({"type": "steps", "items": steps})
        elif t == "link" and b.get("kind") == "tiktok":
            if not HOWTO.search(question):      # soft suggestion, how-to questions only
                continue
            item, _ = matcher.find(b.get("query", ""), "tiktok", lang)
            if item:
                out.append({"type": "link", "kind": "tiktok", "id": item["id"],
                            "title": item["title"], "credit": item["credit"],
                            "url": item["source_url"]})
            else:
                db.log_miss(b.get("query", ""), "tiktok")
    return out
