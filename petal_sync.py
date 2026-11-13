"""
PETAL SYNC — keeps the website's copy of Petal fresh
====================================================
Petal learns on GitHub Actions and publishes to the `petal-data` branch.
This module (started by app.py) checks that branch every few minutes and
downloads the newest model, tokenizer, diary and status.

Environment:
  PETAL_REPO          owner/name of your GitHub repo      (required to enable sync)
  PETAL_BRANCH        default: petal-data
  PETAL_GITHUB_TOKEN  only if the repo is private (a token with read access)
  PETAL_SYNC_MINUTES  default: 20
  PETAL_LIVE_DIR      where the website keeps its copy (default: ./petal_live)

If PETAL_REPO is not set, the website just uses whatever is already in PETAL_LIVE_DIR.
"""

import os
import threading
import time
from pathlib import Path

import requests

BASE = Path(__file__).parent
LIVE_DIR = Path(os.environ.get("PETAL_LIVE_DIR") or BASE / "petal_live")
REPO = os.environ.get("PETAL_REPO", "").strip()
BRANCH = os.environ.get("PETAL_BRANCH", "petal-data").strip()
TOKEN = os.environ.get("PETAL_GITHUB_TOKEN", "").strip()
EVERY = max(5, int(os.environ.get("PETAL_SYNC_MINUTES", "20"))) * 60
FILES = ["petal_model.npz", "petal_tokenizer.json", "petal_diary.jsonl"]   # status is fetched first, separately

_started = False


def _get(name):
    headers = {"Authorization": f"token {TOKEN}"} if TOKEN else {}
    url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{name}"
    return requests.get(url, headers=headers, timeout=60)


def _write_atomic(path, content):
    tmp = Path(str(path) + ".part")
    tmp.write_bytes(content)
    tmp.replace(path)


def sync_once():
    """Returns True if something new was downloaded."""
    if not REPO:
        return False
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    r = _get("petal_status.json")
    if r.status_code != 200:
        print(f"[petal-sync] status not available yet (HTTP {r.status_code})")
        return False
    local = LIVE_DIR / "petal_status.json"
    if local.exists() and local.read_bytes() == r.content:
        return False                                  # nothing changed
    for name in FILES:                                # download everything first...
        fr = _get(name)
        if fr.status_code == 200:
            _write_atomic(LIVE_DIR / name, fr.content)
        elif name == "petal_model.npz":
            print(f"[petal-sync] {name}: HTTP {fr.status_code} (Petal not born yet?)")
    _write_atomic(local, r.content)                   # ...then publish status last
    print("[petal-sync] updated from GitHub")
    return True


def _loop():
    while True:
        try:
            sync_once()
        except Exception as e:
            print(f"[petal-sync] {type(e).__name__}: {e}")
        time.sleep(EVERY)


def start():
    global _started
    if _started or not REPO:
        return
    _started = True
    threading.Thread(target=_loop, daemon=True, name="petal-sync").start()
    print(f"[petal-sync] following {REPO}@{BRANCH} every {EVERY // 60} min")
