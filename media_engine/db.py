import os, sqlite3, json, uuid

DATA_DIR = os.getenv("DAISY_MEDIA_DIR") or os.path.join(
    os.path.dirname(os.environ.get("DAISY_DB_PATH", "")) or os.path.dirname(os.path.abspath(__file__)), "media_data")
DB_PATH = os.path.join(DATA_DIR, "daisy_media.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS items(
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,              -- video | tiktok | image | map
  title TEXT NOT NULL,
  tags TEXT DEFAULT '',            -- space separated
  lang TEXT DEFAULT 'en',
  source_url TEXT,
  thumb TEXT,                      -- relative path under DATA_DIR
  meta TEXT DEFAULT '{}',          -- JSON: embed_url, full image, lat/lng...
  credit TEXT DEFAULT '',
  status TEXT DEFAULT 'active',    -- active | pending | disabled
  tier TEXT DEFAULT 'curated',     -- official | curated | community | auto
  note TEXT DEFAULT '',
  review_by TEXT,                  -- YYYY-MM-DD, item hidden after this date
  taps INTEGER DEFAULT 0,
  helpful INTEGER DEFAULT 0,
  unhelpful INTEGER DEFAULT 0,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS jobs(name TEXT PRIMARY KEY, last_run REAL DEFAULT 0);
CREATE TABLE IF NOT EXISTS quota(day TEXT PRIMARY KEY, used INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS misses(
  query TEXT, kind TEXT, count INTEGER DEFAULT 1,
  last_seen TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(query, kind)
);
"""

def conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.executescript(SCHEMA)
        try:   # migrate databases created before tiers existed
            c.execute("ALTER TABLE items ADD COLUMN tier TEXT DEFAULT 'curated'")
        except sqlite3.OperationalError:
            pass

def add_item(kind, title, tags="", lang="en", source_url=None, thumb=None,
             meta=None, credit="", status="active", review_by=None, tier="curated"):
    item_id = f"{kind[:3]}_{uuid.uuid4().hex[:8]}"
    with conn() as c:
        c.execute(
            "INSERT INTO items(id,kind,title,tags,lang,source_url,thumb,meta,credit,status,review_by,tier)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (item_id, kind, title, tags.lower(), lang, source_url, thumb,
             json.dumps(meta or {}), credit, status, review_by, tier))
    return item_id

def log_miss(query, kind):
    with conn() as c:
        c.execute(
            "INSERT INTO misses(query,kind) VALUES(?,?) "
            "ON CONFLICT(query,kind) DO UPDATE SET count=count+1,last_seen=CURRENT_TIMESTAMP",
            (query.lower().strip(), kind))

def get_item(item_id):
    with conn() as c:
        return c.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

def exists_url(url):
    with conn() as c:
        return c.execute("SELECT 1 FROM items WHERE source_url=?", (url,)).fetchone() is not None

def spend(units):
    """YouTube quota guard. Returns False when today's budget is used up."""
    from datetime import date
    budget = int(os.getenv("DAILY_QUOTA_BUDGET", "8000"))   # free quota is 10,000/day
    day = date.today().isoformat()
    with conn() as c:
        row = c.execute("SELECT used FROM quota WHERE day=?", (day,)).fetchone()
        used = row["used"] if row else 0
        if used + units > budget:
            return False
        c.execute("INSERT INTO quota(day,used) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET used=used+?",
                  (day, units, units))
    return True
