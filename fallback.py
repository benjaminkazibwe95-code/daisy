"""Live fallback, only when the library (official sync + curated) has no match.
Video: search YouTube, score every result automatically, accept the best one only if it clears the bar.
Map: OpenStreetMap geocoding. Both go live immediately, labelled, and expire on their own."""
import asyncio, os, re
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
    thumb = None            # the page loads YouTube's own thumbnail directly: no download, no image processing here
    item_id = db.add_item(
        "video", best["title"], f"{query} {scoring.auto_tags(best['title'], best['description'])}", lang,
        source_url=url, thumb=thumb, credit=best["channel"], tier="community", status="active",
        meta={"embed_url": f"https://www.youtube-nocookie.com/embed/{best['id']}", "remote_thumb": best["thumb_url"],
              "channel_id": best["channel_id"], "views": best["views"], "score": best_score},
        review_by=(date.today() + timedelta(days=90)).isoformat())   # community items expire on their own
    return db.get_item(item_id)

UA = os.getenv("OSM_USER_AGENT", "DaisyMediaEngine/1.0 (hello@trustedbiz.co.ug)")   # Nominatim/Wikimedia ask for a contact in the agent
DIAGRAMISH = re.compile(r"\b(diagram|labell?ed|structure|anatomy|parts? of|cross.?section|map of|flag|coat of arms|logo|chart)\b", re.I)

async def _map(query, lang):
    import httpx
    parts = [p.strip() for p in query.split(",") if p.strip()]
    tries = [", ".join(parts)]
    if len(parts) > 2:
        tries.append(", ".join(parts[1:]))          # name not in OpenStreetMap: fall back to the area (label shows what was found)
    headers = {"User-Agent": UA}
    async with httpx.AsyncClient(timeout=6, headers=headers) as c:
        for q in tries:
            params = {"q": q, "format": "json", "limit": 1}
            if os.getenv("DEFAULT_COUNTRY_CODES"):
                params["countrycodes"] = os.getenv("DEFAULT_COUNTRY_CODES")   # e.g. "ug"
            r = await c.get("https://nominatim.openstreetmap.org/search", params=params)
            if r.status_code != 200:
                print(f"[media] map: OpenStreetMap answered {r.status_code} for '{q[:50]}' (check OSM_USER_AGENT)", flush=True)
                continue
            hits = r.json()
            if hits:
                h = hits[0]
                name = h.get("display_name", q).split(",")[0]
                return db.get_item(db.add_item(
                    "map", name, query, lang, credit="OpenStreetMap", tier="auto",
                    meta={"lat": float(h["lat"]), "lng": float(h["lon"]), "zoom": 16, "label": name}))
    return None

async def _commons(c, query):
    params = {"action": "query", "generator": "search", "gsrnamespace": 6, "gsrsearch": query,
              "gsrlimit": 8, "prop": "imageinfo", "iiprop": "url|mime|extmetadata",
              "iiurlwidth": 1024, "format": "json"}
    r = await c.get("https://commons.wikimedia.org/w/api.php", params=params)
    return commons.parse_commons(r.json(), query) if r.status_code == 200 else None

async def _pexels(c, query):
    key = os.getenv("PEXELS_API_KEY")
    if not key:
        return None
    r = await c.get("https://api.pexels.com/v1/search", params={"query": query, "per_page": 6, "orientation": "landscape"},
                    headers={"Authorization": key})
    return commons.parse_pexels(r.json(), query) if r.status_code == 200 else None

async def _image(query, lang):
    """Both sources are asked at the same moment; real photos (Pexels) win for everyday things, Commons wins for
    diagrams, anatomy, flags and places. The picture is NOT downloaded here: the page loads it straight from the
    source, so the answer's card is ready in about a second. (The source is credited under the picture.)"""
    import httpx
    async with httpx.AsyncClient(timeout=5, headers={"User-Agent": UA}, follow_redirects=True) as c:
        com, pex = await asyncio.gather(_commons(c, query), _pexels(c, query), return_exceptions=True)
    com = None if isinstance(com, Exception) else com
    pex = None if isinstance(pex, Exception) else pex
    hit = (com or pex) if DIAGRAMISH.search(query) else (pex or com)
    if not hit or not str(hit.get("thumb_url", "")).startswith("https://"):
        return None
    return db.get_item(db.add_item(
        "image", hit["title"], query, lang, source_url=hit["page"], thumb=None,
        meta={"remote": hit["thumb_url"]}, credit=hit["credit"], tier="auto",
        review_by=(date.today() + timedelta(days=60)).isoformat()))      # remote links can rot, so these re-check sooner

async def find_pending_or_live(query, kind, lang):
    try:
        if kind == "image":
            return await _image(query, lang)
        if kind == "video":
            return await _youtube(query, lang)
        if kind == "map":
            return await _map(query, lang)
    except Exception as e:
        print(f"[media] live {kind} error: {type(e).__name__}: {str(e)[:160]}", flush=True)
        return None          # fallback (incl. quota exhausted) must never break the answer
    return None
