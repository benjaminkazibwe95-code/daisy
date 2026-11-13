"""
PETAL TRAINER — a small GPT built from scratch, trained on Daisy's data
=======================================================================
Needs: pip install torch numpy

Steps (after daisy_harvest.py has collected data):
  python petal_tokenizer.py train --vocab 4000
  python petal_tokenizer.py tokenize
  python petal_train.py train --size tiny --steps 3000
  python petal_train.py sample --prompt "What is gravity?"

Sizes (rough, with vocab 4000):
  tiny   ~1.5M params   runs on any laptop CPU, good for testing the pipeline
  small  ~10M params    free Colab GPU, or patient CPU
  medium ~30M params    one real GPU, needs 100MB+ of corpus to be worth it

Training resumes automatically from petal.pt if it exists.
"""

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

BASE = Path(__file__).parent
DATA = Path(os.environ.get("PETAL_DATA_DIR") or BASE)
CKPT_FILE = DATA / "petal.pt"
TRAIN_BIN = DATA / "petal_train.bin"
VAL_BIN = DATA / "petal_val.bin"

SIZES = {
    "tiny":   dict(n_layer=4, n_head=4, n_embd=128, block_size=128),
    "small":  dict(n_layer=6, n_head=6, n_embd=384, block_size=256),
    "medium": dict(n_layer=8, n_head=8, n_embd=512, block_size=256),
}


@dataclass
class PetalConfig:
    vocab_size: int = 4000
    block_size: int = 128
    n_layer: int = 4
    n_head: int = 4
    n_embd: int = 128
    dropout: float = 0.1


def _torch():
    try:
        import torch
        return torch
    except ImportError:
        sys.exit("PyTorch is not installed. Run: pip install torch numpy")


def build_model(cfg):
    torch = _torch()
    import torch.nn as nn
    import torch.nn.functional as F

    class Block(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.ln1 = nn.LayerNorm(c.n_embd)
            self.qkv = nn.Linear(c.n_embd, 3 * c.n_embd)
            self.proj = nn.Linear(c.n_embd, c.n_embd)
            self.ln2 = nn.LayerNorm(c.n_embd)
            self.fc = nn.Linear(c.n_embd, 4 * c.n_embd)
            self.out = nn.Linear(4 * c.n_embd, c.n_embd)
            self.n_head, self.drop = c.n_head, c.dropout
            self.resid = nn.Dropout(c.dropout)

        def forward(self, x):
            B, T, C = x.shape
            q, k, v = self.qkv(self.ln1(x)).split(C, dim=2)
            q, k, v = (t.view(B, T, self.n_head, C // self.n_head).transpose(1, 2) for t in (q, k, v))
            y = F.scaled_dot_product_attention(q, k, v, is_causal=True,
                                               dropout_p=self.drop if self.training else 0.0)
            x = x + self.resid(self.proj(y.transpose(1, 2).contiguous().view(B, T, C)))
            return x + self.resid(self.out(F.gelu(self.fc(self.ln2(x)))))

    class Petal(nn.Module):
        def __init__(self, c):
            super().__init__()
            self.cfg = c
            self.tok = nn.Embedding(c.vocab_size, c.n_embd)
            self.pos = nn.Embedding(c.block_size, c.n_embd)
            self.drop = nn.Dropout(c.dropout)
            self.blocks = nn.ModuleList([Block(c) for _ in range(c.n_layer)])
            self.ln_f = nn.LayerNorm(c.n_embd)
            self.head = nn.Linear(c.n_embd, c.vocab_size, bias=False)
            self.head.weight = self.tok.weight           # weight tying
            self.apply(self._init)

        @staticmethod
        def _init(m):
            if isinstance(m, (nn.Linear, nn.Embedding)):
                nn.init.normal_(m.weight, mean=0.0, std=0.02)
                if getattr(m, "bias", None) is not None:
                    nn.init.zeros_(m.bias)

        def forward(self, idx, targets=None):
            B, T = idx.shape
            x = self.drop(self.tok(idx) + self.pos(torch.arange(T, device=idx.device)))
            for b in self.blocks:
                x = b(x)
            logits = self.head(self.ln_f(x))
            loss = None
            if targets is not None:
                loss = F.cross_entropy(logits.view(-1, logits.size(-1)), targets.view(-1))
            return logits, loss

        @torch.no_grad()
        def generate(self, idx, max_new=120, temperature=0.8, top_k=40, stop_id=None):
            for _ in range(max_new):
                logits, _ = self(idx[:, -self.cfg.block_size:])
                logits = logits[:, -1, :] / max(temperature, 1e-5)
                if top_k:
                    v, _ = torch.topk(logits, min(top_k, logits.size(-1)))
                    logits[logits < v[:, [-1]]] = -float("inf")
                nxt = torch.multinomial(F.softmax(logits, dim=-1), 1)
                idx = torch.cat([idx, nxt], dim=1)
                if stop_id is not None and nxt.item() == stop_id:
                    break
            return idx

    return Petal(cfg)


def pick_device():
    torch = _torch()
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def load_checkpoint(device="cpu"):
    torch = _torch()
    ck = torch.load(CKPT_FILE, map_location=device)
    model = build_model(PetalConfig(**ck["config"])).to(device)
    model.load_state_dict(ck["model"])
    model.eval()
    return model, ck


# ---------------------------------------------------------------- training
def train(size, steps, batch, lr, minutes=None):
    torch = _torch()
    import numpy as np
    from petal_tokenizer import PetalTokenizer

    if not TRAIN_BIN.exists():
        sys.exit("No petal_train.bin. Run: python petal_tokenizer.py tokenize")
    tok = PetalTokenizer.load()
    device = pick_device()
    train_data = np.memmap(TRAIN_BIN, dtype=np.uint16, mode="r")
    val_data = np.memmap(VAL_BIN, dtype=np.uint16, mode="r")

    if CKPT_FILE.exists():
        model, ck = load_checkpoint(device)
        cfg, start = model.cfg, ck["step"]
        print(f"Resuming from step {start}")
    else:
        cfg = PetalConfig(vocab_size=tok.vocab_size, **SIZES[size])
        model, ck, start = build_model(cfg).to(device), None, 0
    model.train()
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Petal-{size}: {n_params/1e6:.1f}M params on {device}, {len(train_data):,} train tokens")
    if len(train_data) < cfg.block_size * 10:
        sys.exit("Too little data to train. Harvest more first.")

    opt = torch.optim.AdamW(model.parameters(), lr=lr, betas=(0.9, 0.95), weight_decay=0.1)
    if ck and "opt" in ck:
        opt.load_state_dict(ck["opt"])

    def get_batch(data):
        ix = np.random.randint(0, len(data) - cfg.block_size - 1, batch)
        x = np.stack([data[i:i + cfg.block_size] for i in ix]).astype(np.int64)
        y = np.stack([data[i + 1:i + 1 + cfg.block_size] for i in ix]).astype(np.int64)
        return torch.from_numpy(x).to(device), torch.from_numpy(y).to(device)

    @torch.no_grad()
    def val_loss():
        model.eval()
        losses = [model(*get_batch(val_data))[1].item() for _ in range(20)]
        model.train()
        return sum(losses) / len(losses)

    def save(step):
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(),
                    "config": asdict(cfg), "step": step}, CKPT_FILE)

    warmup, t0 = 100, time.time()
    budget = minutes * 60 if minutes else None
    end = start + steps
    step = start
    while step < end:
        elapsed = time.time() - t0
        if budget and elapsed >= budget:
            break
        prog = (elapsed / budget) if budget else (step - start) / max(1, steps)
        rate = lr * (step - start + 1) / warmup if step - start < warmup else \
            lr * 0.1 + 0.9 * lr * 0.5 * (1 + math.cos(math.pi * min(1.0, prog)))
        for g in opt.param_groups:
            g["lr"] = rate
        _, loss = model(*get_batch(train_data))
        opt.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
        opt.step()
        last_loss = loss.item()

        if step % 50 == 0:
            print(f"step {step:6d}  loss {last_loss:.3f}  {(time.time()-t0):.0f}s", flush=True)
        if step > 0 and step % 500 == 0:
            print(f"  >> val loss {val_loss():.3f}", flush=True)
            save(step)
        step += 1
    end = step
    print(f"  >> val loss {val_loss():.3f}", flush=True)
    save(end)
    print(f"Saved {CKPT_FILE.name} at step {end}. Try: python petal_train.py sample")


def sample(prompt, max_new):
    torch = _torch()
    from petal_tokenizer import PetalTokenizer

    if not CKPT_FILE.exists():
        sys.exit("No petal.pt yet. Train first.")
    device = pick_device()
    model, _ = load_checkpoint(device)
    tok = PetalTokenizer.load()
    ids = tok.encode(f"<|user|>{prompt}<|petal|>")
    out = model.generate(torch.tensor([ids], device=device), max_new=max_new, stop_id=tok.eos)
    print(tok.decode(out[0].tolist()[len(ids):]).replace("<|endoftext|>", "").strip())


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Petal trainer")
    ap.add_argument("cmd", choices=["train", "sample"])
    ap.add_argument("--size", choices=list(SIZES), default="tiny")
    ap.add_argument("--steps", type=int, default=3000)
    ap.add_argument("--minutes", type=float, default=None, help="train for this long instead of a fixed step count")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--prompt", default="What is water?")
    ap.add_argument("--max_new", type=int, default=120)
    a = ap.parse_args()
    if a.cmd == "train":
        train(a.size, a.steps, a.batch, a.lr, a.minutes)
    else:
        sample(a.prompt, a.max_new)
