"""Nightly link check. Cron: 0 3 * * * python health.py
Disables YouTube/TikTok items whose video was removed or made private."""
import asyncio
from . import db

OEMBED = {"video": "https://www.youtube.com/oembed", "tiktok": "https://www.tiktok.com/oembed"}

async def main():
    import httpx
    with db.conn() as c:
        rows = c.execute("SELECT id,kind,source_url FROM items WHERE status='active' AND kind IN ('video','tiktok')").fetchall()
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as http:
        for r in rows:
            try:
                resp = await http.get(OEMBED[r["kind"]], params={"url": r["source_url"], "format": "json"})
                dead = resp.status_code in (400, 401, 403, 404)
            except httpx.HTTPError:
                continue                      # network issue: don't punish the item
            if dead:
                with db.conn() as c:
                    c.execute("UPDATE items SET status='disabled', note='dead link' WHERE id=?", (r["id"],))
                print("disabled", r["id"])

def auto_demote():
    """No one sitting there: items users keep rating unhelpful switch themselves off."""
    with db.conn() as c:
        c.execute("UPDATE items SET status='disabled', note='rated unhelpful' WHERE status='active' "
                  "AND unhelpful>helpful AND unhelpful >= CASE WHEN tier='community' THEN 2 ELSE 4 END")

if __name__ == "__main__":
    auto_demote()
    asyncio.run(main())
