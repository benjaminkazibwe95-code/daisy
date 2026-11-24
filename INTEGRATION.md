# Daisy Media Engine: how it's wired in

## What changed in your repo
NEW   media_engine/        the engine (library, matcher, scoring, discovery, sync, scheduler)
NEW   media_bp.py          Flask blueprint: routes + starts the scheduler
NEW   test_media.py        run: python test_media.py
EDIT  app.py               +7 lines after the Petal block (register blueprint; wrapped in try/except so Daisy always boots)
EDIT  requirements.txt     +httpx, +pillow
EDIT  templates/index.html cards, popups, "Pictures & videos" setting, thumbs feedback
(app.py.diff / index.html.diff / requirements.diff show every change.)
Android: Daisy.apk loads this same web page, so the Android app gets the cards with no separate app work.
The old DaisyNative (Kotlin) project is not covered; it would need its own card UI.

## Environment variables (Render)
YOUTUBE_API_KEY         required for official-channel sync and live video search
DAISY_DB_PATH           you already use /var/data/daisy.db; the media library is stored beside it in /var/data/media_data
                        (without a persistent disk the library resets on restart and is rebuilt from official sources)
OSM_USER_AGENT          e.g. "Daisy/1.0 (you@email.com)"  (OpenStreetMap + Wikimedia require it)
DEFAULT_COUNTRY_CODES   ug   (keeps live map search inside Uganda)
DAILY_QUOTA_BUDGET      default 8000 (YouTube free quota is 10,000 a day)
DAISY_MEDIA_AUTO        default on. Set 0 to stop the automatic jobs
Optional: OFFICIAL_DOMAIN_SUFFIXES (default .go.ug), CRAWL_MAX_SITES, CRAWL_PAGES_PER_SITE

## What runs by itself
~1 minute after the server starts, then on schedule (one worker claims each job):
  discover  weekly  crawl seed government sites -> find official YouTube/TikTok accounts, index page images
  sync      daily   pull new videos from official channels, auto-tag, go live
  health    daily   hide dead links, switch off items users rate unhelpful
Edit the starting sites in media_engine/channels.default.json (copied to the data folder on first run).

## How a question becomes cards
Daisy answers exactly as before. After the answer is on screen the page asks /api/media/resolve.
Media is added only for how-to / where / process type questions, only above the match threshold.
Order: official library -> live search (community videos scored automatically, Wikimedia images, OpenStreetMap) -> nothing.
Only a few keywords are matched and logged, never the whole question or who asked.
Settings > "Pictures & videos in answers": Text only / Thumbnails / Full (saved on the device).

## Owner tools (open https://YOUR-SITE/console?key=ADMIN_KEY once in the browser first)
/api/media/admin/dashboard                         what's in the library, top cards, questions with no match, auto-disabled
/api/media/admin/run/discover  (or sync, health)   run a job now
/api/media/admin/item/<id>/disable  (or enable)    kill switch for one card
POST /api/media/admin/ingest-link  {"url": "...", "tags": "business registration"}   add a specific YouTube/TikTok link
