"""Thin YouTube Data API v3 client. Every call goes through the daily quota guard."""
import os
from . import db

API = "https://www.googleapis.com/youtube/v3"

class QuotaExceeded(Exception):
    pass

async def _get(path, params, cost):
    import httpx
    if not db.spend(cost):
        raise QuotaExceeded()
    params["key"] = os.environ["YOUTUBE_API_KEY"]
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{API}/{path}", params=params)
        r.raise_for_status()
        return r.json()

async def resolve_handle(handle):                       # 1 unit
    d = await _get("channels", {"part": "id,snippet", "forHandle": handle.lstrip("@")}, 1)
    it = (d.get("items") or [None])[0]
    return (it["id"], it["snippet"]["title"]) if it else (None, None)

async def uploads(channel_id, pages=2):                 # 1 unit per 50 videos
    playlist = "UU" + channel_id[2:]                    # a channel's uploads playlist
    ids, token = [], None
    for _ in range(pages):
        p = {"part": "contentDetails", "playlistId": playlist, "maxResults": 50}
        if token:
            p["pageToken"] = token
        d = await _get("playlistItems", p, 1)
        ids += [i["contentDetails"]["videoId"] for i in d.get("items", [])]
        token = d.get("nextPageToken")
        if not token:
            break
    return ids

async def video_details(ids):                           # 1 unit per 50
    out = []
    for k in range(0, len(ids), 50):
        d = await _get("videos", {"part": "snippet,statistics", "id": ",".join(ids[k:k + 50])}, 1)
        for it in d.get("items", []):
            sn, st = it["snippet"], it.get("statistics", {})
            thumbs = sn.get("thumbnails", {})
            out.append({
                "id": it["id"], "title": sn.get("title", ""), "description": sn.get("description", ""),
                "channel_id": sn.get("channelId"), "channel": sn.get("channelTitle", ""),
                "published": sn.get("publishedAt", ""),
                "views": int(st.get("viewCount", 0)),
                "likes": int(st["likeCount"]) if "likeCount" in st else None,
                "thumb_url": (thumbs.get("medium") or thumbs.get("default") or {}).get("url"),
            })
    return out

async def channel_subs(channel_ids):                    # 1 unit per 50
    subs = {}
    ids = list(channel_ids)
    for k in range(0, len(ids), 50):
        d = await _get("channels", {"part": "statistics", "id": ",".join(ids[k:k + 50])}, 1)
        for it in d.get("items", []):
            subs[it["id"]] = int(it["statistics"].get("subscriberCount", 0))
    return subs

async def search(query, lang="en"):                     # 100 units
    d = await _get("search", {"part": "snippet", "q": query, "type": "video", "maxResults": 10,
                              "relevanceLanguage": lang, "safeSearch": "strict"}, 100)
    return [i["id"]["videoId"] for i in d.get("items", [])]
