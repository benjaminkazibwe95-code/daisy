"""Wikimedia Commons image search (free, no key). Only freely licensed images are accepted."""
import re
from . import matcher

ALLOWED = re.compile(r"^(CC BY|CC0|Public domain|PD)", re.I)
BLOCKED = re.compile(r"NC|ND")            # non-commercial / no-derivatives are never used

def _strip(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

def parse_commons(data: dict, query: str, min_rel: float = 0.6):
    q = matcher.tokens(query)
    if not q:
        return None
    best = None
    for p in ((data.get("query") or {}).get("pages") or {}).values():
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or ii.get("mime") not in ("image/jpeg", "image/png"):
            continue
        ext = ii.get("extmetadata") or {}
        lic = _strip((ext.get("LicenseShortName") or {}).get("value", ""))
        if not ALLOWED.match(lic) or BLOCKED.search(lic):
            continue
        title = re.sub(r"\.(jpe?g|png)$", "", p.get("title", "").replace("File:", ""), flags=re.I).replace("_", " ")
        desc = _strip((ext.get("ImageDescription") or {}).get("value", ""))
        if "geograph" in title.lower() or "geograph" in (ii.get("descriptionurl") or "").lower():
            continue                           # random UK landscape photos with misleading titles
        hits = q & matcher.tokens(f"{title} {desc[:300]}")
        rel = len(hits) / len(q)
        if rel < min_rel or len(hits) < min(2, len(q)) or (best and rel <= best["score"]):
            continue
        artist = _strip((ext.get("Artist") or {}).get("value", "")) or "Unknown author"
        best = {"title": title, "thumb_url": ii.get("thumburl") or ii.get("url"),
                "page": ii.get("descriptionurl"), "credit": f"{artist} / {lic} (Wikimedia Commons)",
                "score": rel}
    return best


def parse_pexels(data: dict, query: str):
    """Pexels (free licence, no attribution required; we credit the photographer anyway).
    Takes the first result whose description shares a word with the query, else the first result."""
    q = matcher.tokens(query)
    photos = [p for p in ((data or {}).get("photos") or []) if (p.get("src") or {}).get("large")]
    if not photos:
        return None
    pick = next((p for p in photos if q & matcher.tokens(p.get("alt") or "")), None)
    if pick is None:
        # nothing describes the query: only trust the top result when Pexels gave no descriptions at all
        if any((p.get("alt") or "").strip() for p in photos):
            return None
        pick = photos[0]
    return {"title": ((pick.get("alt") or query).strip() or query)[:120], "thumb_url": pick["src"]["large"],
            "page": pick.get("url"), "credit": f"Photo by {pick.get('photographer') or 'a photographer'} / Pexels"}
