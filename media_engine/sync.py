"""Nightly official-channel sync. Cron: 0 2 * * * python sync.py
Pulls each official channel's recent uploads, auto-tags from title/description, goes live with no review."""
import asyncio
from datetime import date, timedelta
from . import db, channels, ingest, scoring, yt

def _row(d, lang):
    return dict(
        kind="video", title=d["title"], tags=scoring.auto_tags(d["title"], d["description"]),
        lang=lang, source_url=f"https://www.youtube.com/watch?v={d['id']}", credit=d["channel"],
        meta={"embed_url": f"https://www.youtube-nocookie.com/embed/{d['id']}",
              "channel_id": d["channel_id"], "views": d["views"], "published": d["published"]},
        status="active", tier="official", review_by=(date.today() + timedelta(days=365)).isoformat())

async def sync_channel(ch):
    ids = await yt.uploads(ch["id"], pages=2)
    fresh = [i for i in ids if not db.exists_url(f"https://www.youtube.com/watch?v={i}")]
    added = 0
    for d in await yt.video_details(fresh):
        if d["title"] in ("Private video", "Deleted video"):
            continue
        row = _row(d, ch.get("lang", "en"))
        row["thumb"] = await ingest.thumb_from_url(d["thumb_url"]) if d["thumb_url"] else None
        db.add_item(**row)
        added += 1
    return added

async def main():
    db.init()
    for ch in channels.load()["official"]:
        try:
            print(ch.get("name", ch["id"]), "->", await sync_channel(ch), "new videos")
        except yt.QuotaExceeded:
            print("quota budget reached, stopping"); break
        except Exception as e:
            print("failed", ch.get("name"), e)

if __name__ == "__main__":
    asyncio.run(main())
