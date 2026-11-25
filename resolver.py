"""Turns Daisy's blocks into final UI blocks: picks media, enforces limits, applies data mode."""
import json, re
from collections import Counter
from . import db, matcher, fallback

LIMITS = {"video": 1, "image": 2, "map": 1}
LIMITS_INLINE = {"video": 1, "image": 2, "map": 1}      # cards placed inside an answer, one per tag Daisy wrote
HOWTO = re.compile(r"\b(how|steps?|guide|register|apply|start|open|get)\b", re.I)

def _popup(item, meta):
    if item["kind"] == "video":
        return {"player": "youtube", "embed_url": meta.get("embed_url")}
    if item["kind"] == "image":
        return {"image": f"/media/{meta['full']}" if meta.get("full") else None,
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
        "thumb": f"/media/{item['thumb']}" if item["thumb"] else None,
        "popup": _popup(item, meta),
    }
    if mode == "full":
        card["preload"] = True
    card["tier"] = item["tier"]
    card["label"] = {"official": "Official", "community": "Community video"}.get(item["tier"])
    if item["status"] == "pending":
        card["unverified"] = True        # live result, not yet reviewed by you
    return card

async def resolve_marks(marks, data_mode="thumbs", lang="en"):
    """One card (or None) per tag Daisy wrote, in the SAME order, so the page can put each card right
    after the step it belongs to. Same order as resolve(): library first, then a live search."""
    used, used_ids, out = Counter(), set(), []
    for m in marks:
        kind, q = m.get("kind"), (m.get("query") or "").strip()
        if data_mode == "text" or kind not in LIMITS_INLINE or used[kind] >= LIMITS_INLINE[kind] or not q:
            out.append(None)
            continue
        # pictures inside an answer must look like real photos or diagrams: never a crawled logo / page image
        item, _ = matcher.find(q, kind, lang, exclude=used_ids, tiers=(("auto", "curated") if kind == "image" else None))
        if not item:
            db.log_miss(q, kind)
            item = await fallback.find_pending_or_live(q, kind, lang)
        if not item:
            out.append(None)
            continue
        used[kind] += 1
        used_ids.add(item["id"])
        out.append(_card(item, data_mode))
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
