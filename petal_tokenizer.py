"""
PETAL TOKENIZER — Daisy's own byte-level BPE, pure Python, no dependencies
==========================================================================
Trains its own vocabulary from your corpus (nothing borrowed).

Usage:
  python petal_tokenizer.py train --vocab 4000      # learn merges from petal_corpus.txt
  python petal_tokenizer.py tokenize                # corpus + pairs -> petal_train.bin / petal_val.bin
  python petal_tokenizer.py test "Hello Daisy"      # round-trip check

Training format the model learns (chat pairs):
  <|user|>QUESTION<|petal|>ANSWER<|endoftext|>
Plain paragraphs are separated by <|endoftext|>.
"""

import heapq
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

BASE = Path(__file__).parent
DATA = Path(os.environ.get("PETAL_DATA_DIR") or BASE)
TOKENIZER_FILE = DATA / "petal_tokenizer.json"
CORPUS_FILE = DATA / "petal_corpus.txt"
PAIR_FILES = [DATA / "petal_pairs.jsonl", DATA / "petal_own_pairs.jsonl"]
OWN_PAIRS_UPSAMPLE = 3          # your own corrections/conversations count extra
TRAIN_BIN = DATA / "petal_train.bin"
VAL_BIN = DATA / "petal_val.bin"

# GPT-2 style pre-split: contractions, words, numbers, symbols, whitespace
_PAT = re.compile(r"'(?:s|t|re|ve|m|ll|d)| ?[^\W\d_]+| ?\d+| ?[^\s\w]+|\s+(?!\S)|\s+")


class PetalTokenizer:
    SPECIALS = ["<|endoftext|>", "<|user|>", "<|petal|>"]

    def __init__(self, merges=None):
        self.merges = [tuple(m) for m in (merges or [])]
        self._build()

    def _build(self):
        self.vocab = {i: bytes([i]) for i in range(256)}
        self.merge_rank = {}
        for i, (a, b) in enumerate(self.merges):
            self.vocab[256 + i] = self.vocab[a] + self.vocab[b]
            self.merge_rank[(a, b)] = i
        base = 256 + len(self.merges)
        self.special = {s: base + i for i, s in enumerate(self.SPECIALS)}
        self.inv_special = {v: k for k, v in self.special.items()}
        self.vocab_size = base + len(self.SPECIALS)
        self.eos = self.special["<|endoftext|>"]
        self._special_re = re.compile("(" + "|".join(re.escape(s) for s in self.SPECIALS) + ")")
        self._cache = {}

    # ------------------------------------------------------------ training
    @classmethod
    def train(cls, text, vocab_size=4000, verbose=True):
        n_merges = vocab_size - 256 - len(cls.SPECIALS)
        counts = Counter(_PAT.findall(text))
        words = [list(w.encode("utf-8")) for w in counts]
        freqs = [counts[w] for w in counts]

        pair_count = Counter()
        where = defaultdict(set)
        for i, w in enumerate(words):
            for p in zip(w, w[1:]):
                pair_count[p] += freqs[i]
                where[p].add(i)
        heap = [(-c, p) for p, c in pair_count.items()]
        heapq.heapify(heap)

        merges = []
        for step in range(n_merges):
            best = None
            while heap:
                negc, p = heapq.heappop(heap)
                if pair_count.get(p, 0) == -negc and -negc > 0:   # not stale
                    best = p
                    break
            if best is None:
                break
            new_id = 256 + len(merges)
            merges.append(best)

            for i in list(where[best]):
                w, f = words[i], freqs[i]
                for p in zip(w, w[1:]):                 # remove old contributions
                    pair_count[p] -= f
                out, j = [], 0
                while j < len(w):
                    if j < len(w) - 1 and (w[j], w[j + 1]) == best:
                        out.append(new_id)
                        j += 2
                    else:
                        out.append(w[j])
                        j += 1
                words[i] = out
                for p in zip(out, out[1:]):             # add new contributions
                    pair_count[p] += f
                    where[p].add(i)
                    heapq.heappush(heap, (-pair_count[p], p))
            pair_count[best] = 0
            if verbose and (step + 1) % 500 == 0:
                print(f"  merges: {step + 1}/{n_merges}")
        return cls(merges)

    # ------------------------------------------------------------ encode / decode
    def _bpe(self, word):
        if word in self._cache:
            return self._cache[word]
        ids = list(word.encode("utf-8"))
        while len(ids) > 1:
            ranked = [(self.merge_rank.get(p, 1 << 30), k) for k, p in enumerate(zip(ids, ids[1:]))]
            rank, k = min(ranked)
            if rank == 1 << 30:
                break
            ids[k:k + 2] = [256 + rank]
        self._cache[word] = ids
        return ids

    def encode(self, text):
        ids = []
        for part in self._special_re.split(text):
            if part in self.special:
                ids.append(self.special[part])
            elif part:
                for w in _PAT.findall(part):
                    ids.extend(self._bpe(w))
        return ids

    def decode(self, ids):
        out = b""
        for i in ids:
            if i in self.inv_special:
                out += self.inv_special[i].encode("utf-8")
            else:
                out += self.vocab.get(i, b"")
        return out.decode("utf-8", errors="replace")

    # ------------------------------------------------------------ save / load
    def save(self, path=TOKENIZER_FILE):
        Path(path).write_text(json.dumps({"merges": self.merges}), encoding="utf-8")

    @classmethod
    def load(cls, path=TOKENIZER_FILE):
        return cls(json.loads(Path(path).read_text(encoding="utf-8"))["merges"])


# ---------------------------------------------------------------- CLI jobs
def cmd_train(vocab_size, sample_mb=8):
    if not CORPUS_FILE.exists():
        sys.exit("No petal_corpus.txt yet. Run: python daisy_harvest.py crawl --pages 100")
    text = CORPUS_FILE.read_text(encoding="utf-8")[: int(sample_mb * 1_000_000)]
    print(f"Training tokenizer on {len(text)/1e6:.1f} MB, vocab {vocab_size}...")
    tok = PetalTokenizer.train(text, vocab_size)
    tok.save()
    print(f"Saved {TOKENIZER_FILE.name} (vocab {tok.vocab_size})")


def _pair_text(p):
    return f"<|user|>{p['instruction']}<|petal|>{p['response']}<|endoftext|>"


def cmd_tokenize():
    import numpy as np

    tok = PetalTokenizer.load()
    docs = []
    if CORPUS_FILE.exists():
        docs += [line.strip() + "<|endoftext|>" for line in CORPUS_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    for f in PAIR_FILES:
        if not f.exists():
            continue
        reps = OWN_PAIRS_UPSAMPLE if "own" in f.name else 1
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                docs += [_pair_text(json.loads(line))] * reps
            except Exception:
                pass
    if not docs:
        sys.exit("Nothing to tokenize. Harvest data first.")

    random.seed(1337)
    random.shuffle(docs)
    ids = []
    for d in docs:
        ids.extend(tok.encode(d))
    arr = np.array(ids, dtype=np.uint16)
    cut = int(len(arr) * 0.95)
    arr[:cut].tofile(TRAIN_BIN)
    arr[cut:].tofile(VAL_BIN)
    print(f"{len(arr):,} tokens  (train {cut:,} / val {len(arr)-cut:,})")


def cmd_test(text):
    tok = PetalTokenizer.load()
    ids = tok.encode(text)
    print(ids)
    print(repr(tok.decode(ids)), "OK" if tok.decode(ids) == text else "MISMATCH")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args:
        sys.exit(__doc__)
    if args[0] == "train":
        v = int(args[args.index("--vocab") + 1]) if "--vocab" in args else 4000
        cmd_train(v)
    elif args[0] == "tokenize":
        cmd_tokenize()
    elif args[0] == "test":
        cmd_test(" ".join(args[1:]) or "Hello Daisy")
    else:
        sys.exit(__doc__)
