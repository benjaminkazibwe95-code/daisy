"""Turns a pasted link or upload into a library item (title, thumbnail, credit)."""
import io, os, re, uuid
from . import db

THUMB_SIZE = (480, 480)
FULL_SIZE = (1280, 1280)

def _save_webp(data: bytes, size, folder: str) -> str:
    from PIL import Image
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail(size)
    rel = f"{folder}/{uuid.uuid4().hex[:10]}.webp"
    os.makedirs(os.path.join(db.DATA_DIR, folder), exist_ok=True)
    img.save(os.path.join(db.DATA_DIR, rel), "WEBP", quality=70)
    return rel

def youtube_id(url: str):
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    return m.group(1) if m else None

async def from_link(url: str) -> dict:
    """YouTube or TikTok link -> item fields. Uses free oEmbed, no API key."""
    import httpx
    if "youtube.com" in url or "youtu.be" in url:
        vid = youtube_id(url)
        if not vid:
            raise ValueError("Could not read the YouTube video id")
        oembed = "https://www.youtube.com/oembed"
        kind, embed = "video", f"https://www.youtube-nocookie.com/embed/{vid}"
    elif "tiktok.com" in url:
        oembed, kind, embed = "https://www.tiktok.com/oembed", "tiktok", None
    else:
        raise ValueError("Only YouTube and TikTok links are supported")

    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(oembed, params={"url": url, "format": "json"})
        r.raise_for_status()
        info = r.json()
        thumb = None
        if info.get("thumbnail_url"):
            t = await c.get(info["thumbnail_url"])
            if t.status_code == 200:
                thumb = _save_webp(t.content, THUMB_SIZE, "thumbs")  # own copy, URLs expire

    return {
        "kind": kind,
        "title": info.get("title") or "Untitled",
        "credit": info.get("author_name", ""),
        "source_url": url,
        "thumb": thumb,
        "meta": {"embed_url": embed, "embed_html": info.get("html") if kind == "tiktok" else None},
    }

def from_image(data: bytes, credit: str = "") -> dict:
    return {
        "thumb": _save_webp(data, THUMB_SIZE, "thumbs"),
        "meta": {"full": _save_webp(data, FULL_SIZE, "full")},
        "credit": credit,
    }

async def thumb_from_url(url: str):
    import httpx
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
        r = await c.get(url)
        return _save_webp(r.content, THUMB_SIZE, "thumbs") if r.status_code == 200 else None
