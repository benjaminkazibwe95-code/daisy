"""
PETAL API — Flask blueprint that puts Petal into Daisy's website
================================================================
  GET  /petal          Petal's page (also used as the small window inside Daisy)
  POST /petal/ask      talk to Petal directly. Her raw answer, nothing hidden
  GET  /petal/status   her age, learning progress
  GET  /petal/diary    how she answered the same questions over time

Also exposes answer_for_chat() so Daisy's chat can use Petal as a model.
No PyTorch needed: the model runs on numpy (petal_infer.py).
"""

import json
import threading
import time
from collections import defaultdict, deque

from flask import Blueprint, jsonify, make_response, render_template, request

import petal_sync

petal_bp = Blueprint("petal", __name__)

RATE_LIMIT, RATE_WINDOW = 12, 60            # messages per visitor per minute
MAX_INPUT = 300
MAX_NEW_TOKENS = 100
NOT_BORN = ("I haven't been born yet. I'm still collecting text and starting to learn. "
            "Come back soon and watch me grow.")

_hits = defaultdict(deque)
_hits_lock = threading.Lock()
_gen_slots = threading.BoundedSemaphore(2)   # at most 2 generations at once
_cache = {"mtime": None, "model": None, "tok": None}
_cache_lock = threading.Lock()


def client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"


def allowed(ip):
    now = time.time()
    with _hits_lock:
        q = _hits[ip]
        while q and now - q[0] > RATE_WINDOW:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return False
        q.append(now)
        if len(_hits) > 5000:                 # keep memory bounded
            for k in [k for k, v in _hits.items() if not v][:1000]:
                _hits.pop(k, None)
    return True


def _paths():
    d = petal_sync.LIVE_DIR
    return d / "petal_model.npz", d / "petal_tokenizer.json", d / "petal_status.json", d / "petal_diary.jsonl"


def _load_model():
    """Returns (model, tokenizer) or (None, None). Reloads when a new model file arrives."""
    npz, tokf, _, _ = _paths()
    if not (npz.exists() and tokf.exists()):
        return None, None
    mtime = npz.stat().st_mtime
    with _cache_lock:
        if _cache["mtime"] != mtime:
            try:
                from petal_infer import PetalModel
                from petal_tokenizer import PetalTokenizer
                _cache.update(model=PetalModel.load(npz), tok=PetalTokenizer.load(tokf), mtime=mtime)
                print("[petal] model loaded")
            except Exception as e:
                print(f"[petal] could not load model: {e}")
                _cache.update(model=None, tok=None, mtime=mtime)
        return _cache["model"], _cache["tok"]


def raw_answer(message):
    """Petal's unfiltered reply, or None if she isn't born yet."""
    model, tok = _load_model()
    if model is None:
        return None
    ids = tok.encode(f"<|user|>{message.strip()[:MAX_INPUT]}<|petal|>")[-(model.block_size - 1):]
    with _gen_slots:
        out = model.generate(ids, max_new=MAX_NEW_TOKENS, temperature=0.8, top_k=40, stop_id=tok.eos)
    text = tok.decode(out).split("<|endoftext|>")[0].split("<|user|>")[0].replace("<|petal|>", "").strip()
    return text or "(Petal said nothing)"


def answer_for_chat(question):
    """Used by Daisy's /ask when the person picks Petal as the response model."""
    return raw_answer(question) or NOT_BORN


def status():
    _, _, sf, _ = _paths()
    try:
        s = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        s = {}
    model, _ = _load_model()
    born = s.get("born")
    return {
        "model_ready": model is not None,
        "day": int((time.time() - born) // 86400) + 1 if (born and model is not None) else 0,
        "step": s.get("step") or 0,
        "loss": s.get("loss"),
        "val_loss": s.get("val_loss"),
        "corpus_mb": s.get("corpus_mb") or 0,
        "goal_mb": 2,
        "params_m": s.get("params_m"),
        "size": s.get("size") or "tiny",
        "phase": s.get("phase") or "starting",
        "updated": s.get("updated"),
    }


def _page(name):
    resp = make_response(render_template(name))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@petal_bp.route("/petal")
def petal_page():
    return _page("petal.html")


@petal_bp.route("/petal/ask", methods=["POST"])
def petal_ask():
    if not allowed(client_ip()):
        return jsonify({"error": "You're sending messages quickly. Give Petal a moment."}), 429
    msg = ((request.get_json(silent=True) or {}).get("message") or "").strip()[:MAX_INPUT]
    if not msg:
        return jsonify({"error": "Say something to Petal."}), 400
    try:
        text = raw_answer(msg)
    except Exception as e:
        print(f"[petal] generation error: {e}")
        return jsonify({"error": "Petal had a problem answering. Try again."}), 500
    return jsonify({"answer": text, "born": text is not None})


@petal_bp.route("/petal/status")
def petal_status():
    return jsonify(status())


@petal_bp.route("/petal/diary")
def petal_diary():
    entries = []
    try:
        for line in _paths()[3].read_text(encoding="utf-8").splitlines():
            entries.append(json.loads(line))
    except Exception:
        pass
    return jsonify({"entries": entries[-300:]})
