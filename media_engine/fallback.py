"""Live fallback, only when the library (official sync + curated) has no match.
Video: search YouTube, score every result automatically, accept the best one only if it clears the bar.
Map: OpenStreetMap geocoding. Both go live immediately, labelled, and expire on their own."""
import os
from datetime import date, timedelta
from . import db, channels, ingest, scoring, yt, commons

async def _youtube(query, lang):
    if not os.getenv("YOUTUBE_API_KEY"):
        return None
    blocked = set(channels.load()["blocked"])
    ids = await yt.search(query, lang)
    if not ids:
        return None
    details = [d for d in await yt.video_details(ids) if d["channel_id"] not in blocked]
    subs = await yt.channel_subs({d["channel_id"] for d in details})
    best, best_score = None, 0.0
    for d in details:
        s, _ = scoring.community_score(query, d, subs.get(d["channel_id"], 0))
        if s > best_score:
            best, best_score = d, s
    if not best or best_score < scoring.MIN_SCORE:
        return None
    url = f"https://www.youtube.com/watch?v={best['id']}"
    existing = db.conn().execute("SELECT id FROM items WHERE source_url=?", (url,)).fetchone()
    if existing:
        return db.get_item(existing["id"])
    thumb = await ingest.thumb_from_url(best["thumb_url"]) if best["thumb_url"] else None
    item_id = db.add_item(
        "video", best["title"], f"{query} {scoring.auto_tags(best['title'], best['description'])}", lang,
        source_url=url, thumb=thumb, credit=best["channel"], tier="community", status="active",
        meta={"embed_url": f"https://www.youtube-nocookie.com/embed/{best['id']}",
              "channel_id": best["channel_id"], "views": best["views"], "score": best_score},
        review_by=(date.today() + timedelta(days=90)).isoformat())   # community items expire on their own
    return db.get_item(item_id)

async def _map(query, lang):
    import httpx
    params = {"q": query, "format": "json", "limit": 1}
    if os.getenv("DEFAULT_COUNTRY_CODES"):
        params["countrycodes"] = os.getenv("DEFAULT_COUNTRY_CODES")   # e.g. "ug"
    headers = {"User-Agent": os.getenv("OSM_USER_AGENT", "DaisyMediaEngine/1.0")}
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        r = await c.get("https://nominatim.openstreetmap.org/search", params=params)
        r.raise_for_status()
        hits = r.json()
    if not hits:
        return None
    h = hits[0]
    name = h.get("display_name", query).split(",")[0]
    return db.get_item(db.add_item(
        "map", name, query, lang, credit="OpenStreetMap", tier="auto",
        meta={"lat": float(h["lat"]), "lng": float(h["lon"]), "zoom": 16, "label": name}))

async def _image(query, lang):
    import httpx
    params = {"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
              "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
              "iiurlwidth": 1280, "format": "json"}
    headers = {"User-Agent": os.getenv("OSM_USER_AGENT", "DaisyMediaEngine/1.0")}
    async with httpx.AsyncClient(timeout=20, headers=headers, follow_redirects=True) as c:
        r = await c.get("https://commons.wikimedia.org/w/api.php", params=params)
        r.raise_for_status()
        hit = commons.parse_commons(r.json(), query)
        if not hit:
            return None
        img = await c.get(hit["thumb_url"])
        if img.status_code != 200:
            return None
    info = ingest.from_image(img.content, hit["credit"])
    return db.get_item(db.add_item(
        "image", hit["title"], query, lang, source_url=hit["page"], thumb=info["thumb"],
        meta=info["meta"], credit=hit["credit"], tier="auto",
        review_by=(date.today() + timedelta(days=180)).isoformat()))

async def find_pending_or_live(query, kind, lang):
    try:
        if kind == "image":
            return await _image(query, lang)
        if kind == "video":
            return await _youtube(query, lang)
        if kind == "map":
            return await _map(query, lang)
    except Exception:
        return None          # fallback (incl. quota exhausted) must never break the answer
    return None
