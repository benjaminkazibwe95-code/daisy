"""Automatic discovery of OFFICIAL sources. Weekly cron: 0 1 * * 0 python discover.py
Trust anchor: a site on an official domain suffix (default .go.ug) is the agency itself.
Whatever YouTube/TikTok accounts that site links from its HOMEPAGE are treated as its official accounts.
Also indexes pages (title + image) from those sites as image cards. Respects robots.txt."""
import asyncio, os, re
from collections import Counter
from datetime import date, timedelta
from html import unescape
from urllib import robotparser
from urllib.parse import urljoin, urlparse
from . import db, channels, ingest, scoring

UA = os.getenv("CRAWL_USER_AGENT", "DaisyMediaBot/1.0")
SUFFIXES = tuple(s.strip() for s in os.getenv("OFFICIAL_DOMAIN_SUFFIXES", ".go.ug").split(",") if s.strip())
PAGES_SEED = int(os.getenv("CRAWL_PAGES_PER_SITE", "15"))
PAGES_FOUND = 5                                      # sites found via links get a lighter crawl
MAX_SITES = int(os.getenv("CRAWL_MAX_SITES", "40"))

HREF = re.compile(r'href=["\']([^"\'#]+)', re.I)
YT = re.compile(r'https?://(?:www\.|m\.)?youtube\.com/(@[\w.\-]+|channel/UC[\w\-]{22})', re.I)
TT = re.compile(r'https?://(?:www\.)?tiktok\.com/(@[\w.\-]+)', re.I)
SKIP_EXT = re.compile(r"\.(pdf|jpe?g|png|gif|docx?|xlsx?|zip|mp4)$", re.I)

def is_official(url):
    return (urlparse(url).hostname or "").endswith(SUFFIXES)

def page_meta(html):
    def m(prop):
        r = (re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)' % prop, html, re.I)
             or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']%s["\']' % prop, html, re.I))
        return unescape(r.group(1)).strip() if r else ""
    t = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return {"title": unescape(t.group(1)).strip() if t else m("og:title"),
            "description": m("og:description") or m("description"),
            "image": m("og:image"), "site": m("og:site_name")}

def find_socials(html):
    return {"youtube": sorted(set(YT.findall(html))), "tiktok": sorted({h.lower() for h in TT.findall(html)})}

class Fetcher:
    def __init__(self, http):
        self.http, self.robots = http, {}

    async def allowed(self, url):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self.robots:
            rp = robotparser.RobotFileParser()
            try:
                r = await self.http.get(base + "/robots.txt")
                rp.parse(r.text.splitlines() if r.status_code == 200 else [])
            except Exception:
                rp.parse([])
            self.robots[base] = rp
        return self.robots[base].can_fetch(UA, url)

    async def get(self, url):
        if not await self.allowed(url):
            return None
        await asyncio.sleep(0.5)                     # be polite
        try:
            r = await self.http.get(url)
            if r.status_code == 200 and "text/html" in r.headers.get("content-type", ""):
                return r.text
        except Exception:
            pass
        return None

async def crawl_site(fetcher, start, max_pages):
    host = urlparse(start).netloc
    queue, seen, pages, others, socials, name = [start], {start}, [], set(), None, ""
    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        html = await fetcher.get(url)
        if not html:
            continue
        mt = page_meta(html)
        if socials is None:                          # homepage only: what the agency itself links to
            socials = find_socials(html)
            name = mt["site"] or re.split(r"\s[-|–—]\s", mt["title"])[0].strip() or host
        pages.append({"url": url, **mt})
        for href in HREF.findall(html):
            link = urljoin(url, href).split("#")[0]
            p = urlparse(link)
            if p.scheme not in ("http", "https"):
                continue
            if p.netloc == host:
                if link not in seen and not SKIP_EXT.search(p.path):
                    seen.add(link)
                    queue.append(link)
            elif is_official(link):
                others.add(f"{p.scheme}://{p.netloc}/")
    return {"name": name, "pages": pages, "socials": socials or {"youtube": [], "tiktok": []}, "others": others}

async def register_socials(site, tags):
    from . import yt
    cfg = channels.load()
    for ref in site["socials"]["youtube"]:
        if not os.getenv("YOUTUBE_API_KEY"):
            break
        cid = ref.split("/")[1] if ref.startswith("channel/") else (await yt.resolve_handle(ref))[0]
        if cid and not any(c["id"] == cid for c in cfg["official"]):
            cfg["official"].append({"id": cid, "name": site["name"], "lang": "en", "found_on": site["pages"][0]["url"]})
    for handle in site["socials"]["tiktok"]:
        if not any(t["handle"] == handle for t in cfg["official_tiktok"]):
            cfg["official_tiktok"].append({"handle": handle, "name": site["name"]})
        url = f"https://www.tiktok.com/{handle}"
        if not db.exists_url(url):                   # official profile as a 'follow' link
            db.add_item("tiktok", f"{site['name']} on TikTok", f"{site['name']} {tags}", "en",
                        source_url=url, credit=handle, tier="official", meta={"profile": True},
                        review_by=(date.today() + timedelta(days=365)).isoformat())
    channels.save(cfg)

async def index_images(http, site):
    """Official pages become image cards: page title + its og:image, credited to the agency."""
    counts = Counter(p["image"] for p in site["pages"] if p["image"])
    added = 0
    for p in site["pages"]:
        # skip generic logos reused across pages, pages with no title, and ones already indexed
        if not p["image"] or counts[p["image"]] > 1 or not p["title"] or db.exists_url(p["url"]):
            continue
        try:
            r = await http.get(urljoin(p["url"], p["image"]))
            if r.status_code != 200:
                continue
            info = ingest.from_image(r.content, site["name"])
        except Exception:
            continue
        db.add_item("image", p["title"], scoring.auto_tags(p["title"], p["description"]), "en",
                    source_url=p["url"], thumb=info["thumb"], meta=info["meta"], credit=site["name"],
                    tier="official", review_by=(date.today() + timedelta(days=180)).isoformat())
        added += 1
    return added

async def main():
    import httpx
    db.init()
    cfg = channels.load()
    queue, done = list(cfg.get("seed_sites", [])), set()
    seeds = set(queue)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": UA}) as http:
        fetcher = Fetcher(http)
        while queue and len(done) < MAX_SITES:
            start = queue.pop(0)
            host = urlparse(start).netloc
            if host in done:
                continue
            done.add(host)
            site = await crawl_site(fetcher, start, PAGES_SEED if start in seeds else PAGES_FOUND)
            if not site["pages"]:
                print("could not read", start); continue
            home = site["pages"][0]
            await register_socials(site, scoring.auto_tags(home["title"], home["description"]))
            imgs = await index_images(http, site)
            print(f"{site['name']}: yt={len(site['socials']['youtube'])} tiktok={len(site['socials']['tiktok'])} images+={imgs}")
            queue += [u for u in site["others"] if urlparse(u).netloc not in done]

if __name__ == "__main__":
    asyncio.run(main())
