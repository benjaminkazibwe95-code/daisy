"""
PETAL INFERENCE — runs Petal with numpy only (no PyTorch needed)
================================================================
The training side (GitHub Actions / your PC) exports petal_model.npz.
Your web server loads that file and generates text with this module, so the
host stays small. petal_export.py proves at export time that this code
produces the same numbers as the PyTorch model.
"""

import json
import math
import os
from pathlib import Path

# On small shared servers, numpy's many BLAS threads fight over a fraction of a CPU
# and run far slower than one thread. Must be set before numpy is imported.
for _v in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np


def _erf(x):
    # Abramowitz & Stegun 7.1.26 (max error ~1.5e-7), keeps us free of scipy
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    y = 1.0 - (((((1.061405429 * t - 1.453152027) * t) + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * np.exp(-a * a)
    return sign * y


def _gelu(x):
    return 0.5 * x * (1.0 + _erf(x / math.sqrt(2.0)))


def _layernorm(x, w, b, eps=1e-5):
    mu = x.mean(-1, keepdims=True)
    var = x.var(-1, keepdims=True)
    return (x - mu) / np.sqrt(var + eps) * w + b


def _softmax(x):
    x = x - x.max(-1, keepdims=True)
    e = np.exp(x)
    return e / e.sum(-1, keepdims=True)


class PetalModel:
    def __init__(self, weights, config):
        self.w = {k: np.asarray(v, dtype=np.float32) for k, v in weights.items()}
        self.cfg = config
        self.block_size = config["block_size"]
        self.n_head = config["n_head"]
        self.n_layer = config["n_layer"]
        self.vocab_size = config["vocab_size"]

    # ------------------------------------------------------------ io
    @classmethod
    def load(cls, path):
        z = np.load(path, allow_pickle=False)
        cfg = json.loads(str(z["config_json"]))
        weights = {k: z[k] for k in z.files if k != "config_json"}
        return cls(weights, cfg)

    @staticmethod
    def save(weights, config, path):
        arrays = {k: np.asarray(v, dtype=np.float16) for k, v in weights.items()}
        np.savez_compressed(path, config_json=np.array(json.dumps(config)), **arrays)

    # ------------------------------------------------------------ forward
    def logits(self, ids):
        """ids: list[int] (length <= block_size). Returns logits for every position, shape (T, vocab)."""
        w = self.w
        ids = np.asarray(ids[-self.block_size:], dtype=np.int64)
        T = len(ids)
        C = w["tok.weight"].shape[1]
        H = self.n_head
        hd = C // H
        x = w["tok.weight"][ids] + w["pos.weight"][:T]
        mask = np.triu(np.ones((T, T), dtype=bool), k=1)
        for i in range(self.n_layer):
            p = f"blocks.{i}."
            h = _layernorm(x, w[p + "ln1.weight"], w[p + "ln1.bias"])
            qkv = h @ w[p + "qkv.weight"].T + w[p + "qkv.bias"]
            q, k, v = np.split(qkv, 3, axis=-1)
            q, k, v = (a.reshape(T, H, hd).transpose(1, 0, 2) for a in (q, k, v))
            att = (q @ k.transpose(0, 2, 1)) / math.sqrt(hd)
            att = np.where(mask, -np.inf, att)
            y = (_softmax(att) @ v).transpose(1, 0, 2).reshape(T, C)
            x = x + (y @ w[p + "proj.weight"].T + w[p + "proj.bias"])
            h = _layernorm(x, w[p + "ln2.weight"], w[p + "ln2.bias"])
            h = _gelu(h @ w[p + "fc.weight"].T + w[p + "fc.bias"])
            x = x + (h @ w[p + "out.weight"].T + w[p + "out.bias"])
        x = _layernorm(x, w["ln_f.weight"], w["ln_f.bias"])
        return x @ w["tok.weight"].T          # tied output head

    def _step(self, tok_id, pos, cache):
        """Run ONE token through the model using cached keys/values. Returns logits (vocab,)."""
        w = self.w
        C = w["tok.weight"].shape[1]
        H = self.n_head
        hd = C // H
        x = w["tok.weight"][tok_id] + w["pos.weight"][pos]
        for i in range(self.n_layer):
            p = f"blocks.{i}."
            h = _layernorm(x, w[p + "ln1.weight"], w[p + "ln1.bias"])
            q, k, v = np.split(h @ w[p + "qkv.weight"].T + w[p + "qkv.bias"], 3)
            q, k, v = (a.reshape(H, 1, hd) for a in (q, k, v))
            cache["k"][i] = k if cache["k"][i] is None else np.concatenate([cache["k"][i], k], axis=1)
            cache["v"][i] = v if cache["v"][i] is None else np.concatenate([cache["v"][i], v], axis=1)
            att = (q @ cache["k"][i].transpose(0, 2, 1)) / math.sqrt(hd)       # (H, 1, t)
            y = (_softmax(att) @ cache["v"][i]).reshape(C)
            x = x + (y @ w[p + "proj.weight"].T + w[p + "proj.bias"])
            h = _layernorm(x, w[p + "ln2.weight"], w[p + "ln2.bias"])
            x = x + (_gelu(h @ w[p + "fc.weight"].T + w[p + "fc.bias"]) @ w[p + "out.weight"].T + w[p + "out.bias"])
        return _layernorm(x, w["ln_f.weight"], w["ln_f.bias"]) @ w["tok.weight"].T

    def _sample(self, lg, temperature, top_k, rng):
        lg = lg / max(temperature, 1e-5)
        if top_k and top_k < lg.shape[0]:
            cut = np.partition(lg, -top_k)[-top_k]
            lg = np.where(lg < cut, -np.inf, lg)
        return int(rng.choice(lg.shape[0], p=_softmax(lg)))

    def generate(self, ids, max_new=120, temperature=0.8, top_k=40, stop_id=None, rng=None):
        """Same text as before, but each new token costs one small step instead of a full re-read."""
        rng = rng or np.random.default_rng()
        ids = list(ids)[-(self.block_size - 1):]
        cache = {"k": [None] * self.n_layer, "v": [None] * self.n_layer}
        lg = None
        for pos, t in enumerate(ids):                       # read the prompt once
            lg = self._step(t, pos, cache)
        out = []
        for _ in range(max_new):
            nxt = self._sample(lg, temperature, top_k, rng)
            out.append(nxt)
            if stop_id is not None and nxt == stop_id:
                break
            ids.append(nxt)
            if len(ids) < self.block_size:
                lg = self._step(nxt, len(ids) - 1, cache)   # fast path
            else:                                           # context full: slide the window (rare)
                lg = self.logits(ids)[-1]
        return out
