import json, os, shutil
from . import db

DEFAULT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "channels.default.json")

def _path():
    return os.getenv("CHANNELS_FILE") or os.path.join(db.DATA_DIR, "channels.json")

def load():
    p = _path()
    if not os.path.exists(p):
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copy(DEFAULT, p)
    with open(p) as f:
        cfg = json.load(f)
    for k in ("seed_sites", "official", "official_tiktok", "blocked"):
        cfg.setdefault(k, [])
    return cfg

def save(cfg):
    with open(_path(), "w") as f:
        json.dump(cfg, f, indent=2)
