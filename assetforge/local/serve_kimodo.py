"""Local Kimodo server — on-prem twin of ``modal/kimodo_app.py``.

Runs NVIDIA Kimodo on the local GPU (RTX 4080) and exposes the SAME HTTP
contract the AssetForge client already speaks:

    POST /generate  {"prompt": "...", "num_frames": 196}  -> NPZ bytes (direct)
    GET  /health                                            -> {"ok": true, ...}

The AssetForge client (``core/backends/kimodo/kimodo.py``) defaults to
``http://localhost:9551`` and reads the NPZ straight from the POST response on
the local path (no Modal 303-poll dance), so pointing AssetForge here = unset
``ASSETFORGE_KIMODO_URL`` (or set it to ``http://localhost:9551``).

Text encoder runs on CPU (``TEXT_ENCODER_DEVICE=cpu``) so the Llama-3 encoder
lives in RAM and the 4080's VRAM (<3 GB) is left for the motion model. First
generation downloads the gated weights (~16 GB) — run ``hf auth login`` once.

Model: **Kimodo-SOMA-RP-v1.1** — 700 h Rigplay studio mocap; SOMA skeleton
matches our SOMA->Mixamo retarget in ``core/backends/kimodo/kimodo.py``.

Run (in the ``kimodo`` conda env):
    conda run -n kimodo python assetforge/local/serve_kimodo.py
"""
import os

# Must precede the kimodo import: forces the Llama-3 text encoder onto the CPU
# (encoder in RAM, motion model on the GPU) so it fits a 16 GB card.
os.environ.setdefault("TEXT_ENCODER_DEVICE", "cpu")

import io
import threading

import numpy as np
import torch
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from kimodo.scripts.generate import load_model

HOST = "127.0.0.1"
PORT = 9551
MODEL_NAME = "kimodo-soma-rp-v1.1"   # pinned: 700 h Rigplay studio mocap, SOMA skeleton

# ── Quality defaults (the Tier-1 upgrades over the old Modal-parity settings) ──
DENOISING_STEPS = 150       # was 50 on Modal; local is free so favour quality (docs: 100-200)
CFG_TYPE = "separated"      # independent [text, constraint] guidance
CFG_WEIGHT = [2.0, 2.0]     # raise the 2nd (constraint) weight once authored poses are added
POST_PROCESSING = True      # foot-skate + constraint cleanup + floor snap (important for retarget)

_cache: dict = {}
_lock = threading.Lock()    # serialize GPU inference — one 4080, model not thread-safe


def _model():
    """Lazily load + cache the model (first call triggers the gated weight download)."""
    if "m" not in _cache:
        device = "cuda:0" if torch.cuda.is_available() else "cpu"
        print(f"[kimodo-local] loading {MODEL_NAME} on {device} (text encoder on CPU) ...")
        _cache["m"] = load_model(MODEL_NAME, device=device)
        print("[kimodo-local] model ready")
    return _cache["m"]


def _generate(prompt: str, num_frames: int) -> bytes:
    model = _model()
    with _lock:
        out = model(
            [prompt], [num_frames],
            num_denoising_steps=DENOISING_STEPS,
            constraint_lst=[],
            cfg_type=CFG_TYPE,
            cfg_weight=CFG_WEIGHT,
            num_samples=1,
            return_numpy=True,
            post_processing=POST_PROCESSING,
        )
    buf = io.BytesIO()
    np.savez(buf, **{k: v for k, v in out.items() if isinstance(v, np.ndarray)})
    return buf.getvalue()


app = FastAPI(title="Kimodo Local")


class Req(BaseModel):
    prompt: str
    num_frames: int = 196


@app.get("/health")
def health():
    return {"ok": True, "cuda": torch.cuda.is_available(), "model": MODEL_NAME}


@app.post("/generate")
def generate(req: Req):
    try:
        return Response(_generate(req.prompt, req.num_frames),
                        media_type="application/octet-stream")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(exc))


if __name__ == "__main__":
    print(f"[kimodo-local] serving on http://{HOST}:{PORT}  (model loads on first /generate)")
    uvicorn.run(app, host=HOST, port=PORT)
