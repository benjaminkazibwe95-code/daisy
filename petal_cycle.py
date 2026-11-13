"""
PETAL CYCLE — one full round of learning. Run by GitHub Actions every few
hours (see .github/workflows/petal.yml), or by hand:

    python petal_cycle.py --harvest-minutes 15 --train-minutes 300

  1. harvest   read more pages into the corpus
  2. tokenize  (the tokenizer is trained once, then kept)
  3. train     resume from petal.pt for a fixed amount of time
  4. export    petal_model.npz for the web server (with a PyTorch parity check)
  5. diary     ask Petal the same questions as always, write down her raw answers
  6. status    petal_status.json for the website

Everything lives in PETAL_DATA_DIR (default ./petal_data).
Needs: torch, numpy, requests, beautifulsoup4
"""

import argparse
import gzip
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
DATA = Path(os.environ.get("PETAL_DATA_DIR") or BASE / "petal_data")
os.environ["PETAL_DATA_DIR"] = str(DATA)
os.environ.setdefault("PETAL_MAX_CORPUS_MB", "60")      # keeps every stored file well under GitHub's 100 MB limit
DATA.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(BASE))

STATUS = DATA / "petal_status.json"
DIARY = DATA / "petal_diary.jsonl"
MODEL_NPZ = DATA / "petal_model.npz"
MIN_CORPUS_BYTES = 2_000_000
PACKED = ["petal_corpus.txt", "petal_pairs.jsonl"]          # stored gzipped between runs
TRANSIENT = ["petal_train.bin", "petal_val.bin", "petal_own_pairs.jsonl"]   # rebuilt every run; own pairs never published

DIARY_PROMPTS = [
    "What is water?",
    "Who are you?",
    "What is gravity?",
    "Tell me about Africa.",
    "How are you?",
    "What is 2 + 2?",
]


def log(msg):
    print(f"[cycle] {msg}", flush=True)


def _gunzip(name):
    gz, plain = DATA / (name + ".gz"), DATA / name
    if gz.exists() and not plain.exists():
        with gzip.open(gz, "rb") as src, open(plain, "wb") as dst:
            shutil.copyfileobj(src, dst)
        gz.unlink()


def _gzip(name):
    plain, gz = DATA / name, DATA / (name + ".gz")
    if plain.exists():
        with open(plain, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
            shutil.copyfileobj(src, dst)
        plain.unlink()


def _load_status():
    try:
        return json.loads(STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_status(**kw):
    s = _load_status()
    s.update(kw)
    s["updated"] = time.time()
    STATUS.write_text(json.dumps(s), encoding="utf-8")


def train_subprocess(size, minutes):
    cmd = [sys.executable, "-u", str(BASE / "petal_train.py"), "train", "--size", size,
           "--steps", "1000000000", "--minutes", str(minutes)]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         encoding="utf-8", errors="replace", env=os.environ.copy())
    loss = val = params = None
    for line in p.stdout:
        print(line, end="", flush=True)
        m = re.match(r"step\s+\d+\s+loss\s+([\d.]+)", line)
        if m:
            loss = float(m.group(1))
        v = re.search(r"val loss\s+([\d.]+)", line)
        if v:
            val = float(v.group(1))
        q = re.search(r"([\d.]+)M params", line)
        if q:
            params = float(q.group(1))
    if p.wait() != 0:
        sys.exit("[cycle] training failed")
    return loss, val, params


def write_diary(step, loss, val):
    from petal_infer import PetalModel
    from petal_tokenizer import PetalTokenizer

    model = PetalModel.load(MODEL_NPZ)
    tok = PetalTokenizer.load()
    answers = {}
    for q in DIARY_PROMPTS:
        ids = tok.encode(f"<|user|>{q}<|petal|>")[-(model.block_size - 1):]
        out = model.generate(ids, max_new=80, temperature=0.8, top_k=40, stop_id=tok.eos)
        text = tok.decode(out).split("<|endoftext|>")[0].split("<|user|>")[0].replace("<|petal|>", "").strip()
        answers[q] = text
    st = _load_status()
    now = time.time()
    day = int((now - st.get("born", now)) // 86400) + 1
    entry = {"time": now, "day": day, "step": step, "loss": loss, "val_loss": val, "answers": answers}
    with open(DIARY, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    log(f"diary entry written (day {day})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--harvest-minutes", type=float, default=15)
    ap.add_argument("--train-minutes", type=float, default=300)
    ap.add_argument("--size", default="tiny", choices=["tiny"],
                    help="tiny only: bigger checkpoints exceed GitHub's 100 MB file limit")
    a = ap.parse_args()

    for n in PACKED:
        _gunzip(n)

    import daisy_harvest as H
    import petal_tokenizer as T

    log(f"harvesting for up to {a.harvest_minutes:.0f} minutes")
    H.crawl(10 ** 9, max_seconds=a.harvest_minutes * 60)
    H.export_daisy_data()

    corpus = H.CORPUS_FILE.stat().st_size if H.CORPUS_FILE.exists() else 0
    st = _load_status()
    _write_status(phase="collecting", corpus_mb=round(corpus / 1e6, 2), runs=st.get("runs", 0) + 1)

    if corpus < MIN_CORPUS_BYTES:
        log(f"corpus {corpus/1e6:.2f} MB < {MIN_CORPUS_BYTES/1e6:.0f} MB: still collecting")
    else:
        if not T.TOKENIZER_FILE.exists():
            log("training the tokenizer (one time)")
            T.cmd_train(4000)
        log("tokenizing")
        T.cmd_tokenize()
        log(f"training for {a.train_minutes:.0f} minutes")
        loss, val, params = train_subprocess(a.size, a.train_minutes)

        import petal_export
        step = petal_export.export()
        born = _load_status().get("born") or time.time()
        _write_status(born=born)
        write_diary(step, loss, val)
        _write_status(phase="learning", model_ready=True, step=step, loss=loss, val_loss=val,
                      params_m=params, size=a.size, corpus_mb=round(corpus / 1e6, 2))

    for n in PACKED:
        _gzip(n)
    for n in TRANSIENT:
        (DATA / n).unlink(missing_ok=True)
    log("done")


if __name__ == "__main__":
    main()
