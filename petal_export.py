"""
PETAL EXPORT — turns the training checkpoint (petal.pt, needs torch) into
petal_model.npz that the web server can run with numpy only.

Refuses to export if the numpy engine disagrees with the PyTorch model.
"""

import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).parent
DATA = Path(os.environ.get("PETAL_DATA_DIR") or BASE)


def export(ckpt=None, out=None):
    import numpy as np
    import torch
    import petal_train
    from petal_infer import PetalModel

    ckpt = Path(ckpt or DATA / "petal.pt")
    out = Path(out or DATA / "petal_model.npz")
    model, ck = petal_train.load_checkpoint("cpu")
    cfg = ck["config"]

    weights = {k: v.detach().cpu().numpy().astype("float32")
               for k, v in model.state_dict().items() if k != "head.weight"}   # head is tied to tok.weight

    # --- parity check: numpy engine vs PyTorch on a fixed input ---
    ids = [(i * 7 + 3) % cfg["vocab_size"] for i in range(min(40, cfg["block_size"]))]
    with torch.no_grad():
        ref = model(torch.tensor([ids]))[0][0].numpy()
    got = PetalModel(weights, cfg).logits(ids)
    err = float(np.abs(ref - got).max())
    print(f"[export] parity max abs diff: {err:.2e}")
    if err > 5e-3:
        sys.exit(f"[export] REFUSING to export: numpy engine disagrees with PyTorch (diff {err:.3g}).")

    PetalModel.save(weights, cfg, out)
    print(f"[export] wrote {out.name} ({out.stat().st_size/1e6:.1f} MB)")
    return ck["step"]


if __name__ == "__main__":
    export()
