"""Runs the nightly/weekly jobs inside Daisy's own server, so nobody has to set up cron.
Safe with several workers: a job is claimed in the database, so only one worker runs it."""
import asyncio, os, threading, time
from . import db, discover, sync, health

JOBS = {                       # name: (every N seconds, function)
    "discover": (7 * 86400, lambda: asyncio.run(discover.main())),
    "sync":     (86400,     lambda: asyncio.run(sync.main())),
    "health":   (86400,     lambda: (health.auto_demote(), asyncio.run(health.main()))),
}

def _claim(name, every):
    now = time.time()
    with db.conn() as c:
        c.execute("INSERT OR IGNORE INTO jobs(name,last_run) VALUES(?,0)", (name,))
        cur = c.execute("UPDATE jobs SET last_run=? WHERE name=? AND last_run<=?", (now, name, now - every))
        return cur.rowcount == 1

def run_job(name):
    try:
        JOBS[name][1]()
        print(f"[media] job {name} done")
    except Exception as e:
        print(f"[media] job {name} failed: {e}")

def _loop():
    time.sleep(60)             # let the server finish booting first
    while True:
        for name, (every, _) in JOBS.items():
            if _claim(name, every):
                run_job(name)
        time.sleep(3600)

def start():
    if os.environ.get("DAISY_MEDIA_AUTO", "1") == "0":
        return
    threading.Thread(target=_loop, daemon=True, name="media-scheduler").start()
