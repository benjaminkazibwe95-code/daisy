"""Run:  python test_media.py     (no network needed)"""
import asyncio, os, tempfile
os.environ["DAISY_MEDIA_DIR"] = tempfile.mkdtemp()
os.environ["ADMIN_KEY"] = "x"
from flask import Flask
from media_engine import db, resolver, matcher, scoring, commons, discover, auto, scheduler, fallback
import media_bp

db.init()
R = lambda *a: asyncio.run(resolver.resolve(*a))

# ---- library + matching
vid = db.add_item("video", "How to register your business with URSB", "business registration uganda ursb", tier="official", credit="URSB",
                  source_url="https://youtube.com/watch?v=aaaaaaaaaaa", meta={"embed_url": "https://www.youtube-nocookie.com/embed/aaaaaaaaaaa"})
os.makedirs(os.path.join(db.DATA_DIR, "thumbs"), exist_ok=True)
open(os.path.join(db.DATA_DIR, "thumbs", "t.webp"), "wb").write(b"img")
img = db.add_item("image", "URSB name reservation portal", "ursb name reservation business", tier="official", thumb="thumbs/t.webp",
                  meta={"full": "thumbs/t.webp"}, credit="URSB", source_url="https://ursb.go.ug/reserve")
db.add_item("tiktok", "URSB on TikTok", "ursb business registration", tier="official", source_url="https://www.tiktok.com/@ursb", credit="@ursb")
db.add_item("video", "Old scam video", "business registration", status="disabled")

# ---- auto: only when media clearly helps; keywords only, never the full question
assert auto.build_blocks("I feel tired today, any advice?") == []
assert auto.build_blocks("hi") == []
q = "How do I register my business in Uganda with URSB please?"
assert auto.keywords(q) == "register business uganda ursb please"
out = auto.media_for(q, "thumbs")
kinds = [b["type"] + ":" + b.get("kind", "") for b in out]
assert "card:video" in kinds and "card:image" in kinds and "link:tiktok" in kinds, kinds
assert auto.media_for(q, "text") == []                                   # data saver: nothing
card = next(b for b in out if b["kind"] == "video")
assert card["label"] == "Official" and card["popup"]["embed_url"].endswith("aaaaaaaaaaa")
with db.conn() as c:                                                     # misses log keywords only
    assert all(len(r["query"].split()) <= 6 for r in c.execute("SELECT query FROM misses"))

# ---- scoring / commons / discovery (parsers, no network)
good = {"title": "How to register a business in Uganda", "description": "URSB guide", "views": 52000, "likes": 2100,
        "published": "2026-03-01T00:00:00Z"}
assert scoring.community_score("register business uganda", good, 20000)[0] >= scoring.MIN_SCORE
assert scoring.community_score("register business uganda", dict(good, title="Register business Uganda guaranteed"), 20000)[0] == 0
assert discover.is_official("https://x.ursb.go.ug/a") and not discover.is_official("https://ursb.go.ug.evil.com/")
sample = {"query": {"pages": {"1": {"title": "File:Kampala skyline.jpg", "imageinfo": [{"mime": "image/jpeg", "thumburl": "u", "descriptionurl": "p",
          "extmetadata": {"LicenseShortName": {"value": "CC BY-SA 4.0"}, "Artist": {"value": "Jane"}}}]},
          "2": {"title": "File:Kampala skyline nc.jpg", "imageinfo": [{"mime": "image/jpeg", "thumburl": "u", "descriptionurl": "p",
          "extmetadata": {"LicenseShortName": {"value": "CC BY-NC 4.0"}}}]}}}}
assert commons.parse_commons(sample, "kampala skyline")["credit"].startswith("Jane / CC BY-SA")

# ---- live fallback is cached into the library, and auto-demotion works
calls = []
async def fake_yt(query, lang):
    calls.append(query)
    return db.get_item(db.add_item("video", "How to get a TIN", query, lang, tier="community"))
fallback._youtube = fake_yt
b = [{"type": "media", "kind": "video", "query": "get tin number"}]
assert R(b, "thumbs", "en", "how to get tin")[0]["label"] == "Community video"
R(b, "thumbs", "en", "how to get tin")
assert len(calls) == 1
cm = db.add_item("video", "Bad", "zzz", tier="community")
with db.conn() as c:
    c.execute("UPDATE items SET unhelpful=3 WHERE id=?", (cm,))
from media_engine import health; health.auto_demote()
assert db.get_item(cm)["status"] == "disabled"

# ---- scheduler claims a job once
assert scheduler._claim("t", 3600) and not scheduler._claim("t", 3600)

# ---- Flask blueprint
app = Flask(__name__); app.secret_key = "s"; app.register_blueprint(media_bp.bp)
c = app.test_client()
assert c.get("/media/thumbs/t.webp").data == b"img"
assert c.get("/media/daisy_media.db").status_code == 404                 # database never exposed
for evil in ("/media/thumbs/../daisy_media.db", "/media/thumbs/%2e%2e/daisy_media.db", "/media/thumbs/..%2fdaisy_media.db", "/media/thumbs/x.db"):
    assert c.get(evil).status_code == 404, evil
assert c.get("/media/channels.json").status_code == 404
r = c.post("/api/media/resolve", json={"question": q, "data_mode": "thumbs"}).get_json()
assert any(x["type"] == "card" for x in r["blocks"])
assert c.post("/api/media/resolve", json={"question": q, "data_mode": "text"}).get_json()["blocks"] == []
assert c.post("/api/media/feedback", json={"item_ids": [vid, "'; DROP TABLE items;--"], "helpful": True}).status_code == 200
assert db.get_item(vid)["helpful"] == 1
assert c.get("/api/media/admin/dashboard").status_code == 403
with c.session_transaction() as s: s["admin"] = True
d = c.get("/api/media/admin/dashboard").get_json()
assert d["counts"] and "top_misses" in d
assert c.post(f"/api/media/admin/item/{vid}/disable").get_json()["status"] == "disabled"
assert not any(x.get("id") == vid for x in auto.media_for(q, "thumbs"))  # kill switch works instantly
print("all checks passed")

# ---- regression: the wrong cards seen in the screenshots (one shared word must not make a match)
arb = db.add_item("image", "About ARB - Architects Registration Board", "architects registration board", tier="official", credit="ARB", source_url="https://arb.go.ug/about")
nita = db.add_item("image", "National Information Technology Authority - Uganda (NITA-U) Official Website", "nita information technology uganda", tier="official", credit="NITA-U", source_url="https://nita.go.ug/")
pod = db.add_item("video", "Women Who Lead | Susan Nava on Business, Growth & Entrepreneurship in Uganda", "women lead business growth entrepreneurship uganda", tier="official", credit="MoICT", source_url="https://youtube.com/watch?v=bbbbbbbbbbb", meta={"embed_url": "x"})
for it in (arb, nita, pod):
    row = db.get_item(it)
    assert matcher.score("hey register business uganda", row, "en") == 0.0, row["title"]
assert matcher.score("register business uganda ursb please", db.get_item(vid), "en") >= matcher.THRESHOLD   # the right card still wins
print("regression checks passed")

# ---- inline tags: one card per tag, in order, nothing leaks in text mode, junk is dropped
v2 = db.add_item("video", "How to register a business name with URSB", "register business name ursb", tier="official", credit="URSB", source_url="https://youtube.com/watch?v=ccccccccccc", meta={"embed_url": "https://www.youtube-nocookie.com/embed/ccccccccccc"})
i2 = db.add_item("image", "URSB name reservation page", "ursb name reservation business", tier="official", thumb="thumbs/t.webp", meta={"full": "thumbs/t.webp"}, credit="URSB", source_url="https://ursb.go.ug/r2")
cards = auto.media_for_marks([{"kind": "video", "query": "register business name ursb"}, {"kind": "image", "query": "ursb name reservation business"},
                              {"kind": "video", "query": "zzz qqq unknown thing"}, {"kind": "bad", "query": "x"}], "thumbs")
assert cards[0]["id"] == v2 and cards[0]["label"] == "Official", cards
assert cards[1]["kind"] == "image" and cards[1]["tier"] == "official", cards
assert len(cards) == 3                      # the bad kind was dropped, the unmatched one stays as None in place
assert auto.media_for_marks([{"kind": "video", "query": "register business name ursb"}], "text") == [None]
assert auto.clean_marks([{"kind": "map", "query": "URSB <script> office, Kampala!"}])[0]["query"] == "URSB script office Kampala"
c = bp_client = Flask(__name__)
c.register_blueprint(media_bp.bp)
r = c.test_client().post("/api/media/resolve", json={"marks": [{"kind": "video", "query": "register business name ursb"}], "data_mode": "thumbs"})
assert r.get_json()["cards"][0]["kind"] == "video"
print("inline checks passed")
