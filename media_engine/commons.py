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
        rel = len(q & matcher.tokens(f"{title} {desc[:300]}")) / len(q)
        if rel < min_rel or (best and rel <= best["score"]):
            continue
        artist = _strip((ext.get("Artist") or {}).get("value", "")) or "Unknown author"
        best = {"title": title, "thumb_url": ii.get("thumburl") or ii.get("url"),
                "page": ii.get("descriptionurl"), "credit": f"{artist} / {lic} (Wikimedia Commons)",
                "score": rel}
    return best
