"""Modal deployment for Kimodo text-to-motion generation.

SETUP (one-time):
  1. Install Modal:   pip install modal
  2. Authenticate:    modal setup        (opens browser — one time only)
  3. Create HF secret in Modal dashboard:
       Name: huggingface   Key: HF_TOKEN   Value: hf_...
     (needed to download the gated Llama-3 text encoder on first run)

DEPLOY:
  modal deploy assetforge/modal/kimodo_app.py

  Modal prints a URL like:
    https://<username>--last-rite-kimodo-kimodo-api.modal.run

  Set that as the Kimodo endpoint in AssetForge:
    Windows:  $env:ASSETFORGE_KIMODO_URL = "https://..."
    Or add it to your Blender launch script / system env vars.

API (identical to the local Docker container):
  POST /generate  {"prompt": "character does a heavy overhead slam"}
                  -> NPZ binary (SOMA skeleton, 77 joints @ 30 fps)
  GET  /health    -> {"status": "ok"}

COLD START:
  Gated model weights (Llama-3-8B for the text encoder) are downloaded once
  and cached in the kimodo-weights Volume at /hf-cache.  First call may take
  5-10 min; warm calls ~30 s.
"""

import modal

# ── Image ─────────────────────────────────────────────────────────────────────
# TEXT_ENCODER_DEVICE=cpu offloads the Llama-3 text encoder to RAM, leaving the
# full A10G VRAM for the motion diffusion model.
# HF_HOME / HF_HUB_CACHE point to the mounted Volume so gated weights are
# downloaded once and reused across cold starts.
image = (
    modal.Image.from_registry("ghcr.io/eyalenav/kimodo-api:latest")
    # transformer_engine is incompatible with this PyTorch alpha build — remove it.
    .run_commands("pip uninstall -y transformer-engine || true")
    # PyTorch 2.7.0a0 (in this NVIDIA image) is missing torch.float8_e8m0fnu which
    # peft 0.18+ requires at import time.  Write a .pth startup file that aliases it
    # to float8_e4m3fn so peft's capability-detection succeeds at import.
    # Site-packages path confirmed from container tracebacks.
    .run_commands(
        "echo \"import torch; [setattr(torch, a, getattr(torch, 'float8_e4m3fn', torch.float16)) "
        "for a in ['float8_e8m0fnu', 'float8_e8m0fnu_t'] if not hasattr(torch, a)]\" "
        "> /usr/local/lib/python3.12/dist-packages/00_float8_patch.pth"
    )
    .env({
        "TEXT_ENCODER_DEVICE": "cpu",
        "HF_HOME": "/hf-cache",
        "HF_HUB_CACHE": "/hf-cache/hub",
        "TRANSFORMERS_CACHE": "/hf-cache/hub",
    })
)

# ── Weight cache Volume ────────────────────────────────────────────────────────
# Mounted at /hf-cache (not /root/.cache which is non-empty in the base image).
weights_vol = modal.Volume.from_name("kimodo-weights", create_if_missing=True)

# ── App ───────────────────────────────────────────────────────────────────────
app = modal.App("last-rite-kimodo")


@app.function(
    image=image,
    gpu="A10G",                         # 24 GB VRAM
    timeout=1800,                       # 30 min ceiling — covers cold-start weight download
    max_containers=1,                   # one generation at a time
    scaledown_window=600,               # keep warm 10 min between AssetForge calls
    secrets=[modal.Secret.from_name("huggingface")],
    volumes={"/hf-cache": weights_vol},
)
@modal.asgi_app()
def kimodo_api():
    """ASGI wrapper around Kimodo that streams the NPZ binary directly.

    The bundled server.py saves files to /kimodo_output and returns a JSON path —
    no download endpoint.  This wrapper calls the same model but returns the NPZ
    bytes in the HTTP response body so AssetForge can save it directly.
    """
    import sys, io, asyncio, threading
    sys.path.insert(0, "/workspace")

    import numpy as np
    import torch
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import Response
    from pydantic import BaseModel
    from kimodo.scripts.generate import load_model   # type: ignore[import]

    _cache: dict = {}
    _infer_lock = threading.Lock()   # serialize GPU inference — one A10G, model not thread-safe

    def _get_model():
        if "model" not in _cache:
            device = "cuda:0" if torch.cuda.is_available() else "cpu"
            print(f"[kimodo-api] Loading model on {device} ...")
            _cache["model"], _ = load_model(
                None, device=device,
                default_family="Kimodo", return_resolved_name=True
            )
            print("[kimodo-api] Model loaded ✓")
        return _cache["model"]

    def _generate_npz(prompt: str, num_frames: int) -> bytes:
        model = _get_model()
        with _infer_lock:   # serialize concurrent requests onto the single GPU
            output = model(
                [prompt], [num_frames],
                constraint_lst=[],
                num_denoising_steps=50,
                num_samples=1,
                multi_prompt=True,
                num_transition_frames=0,
                post_processing=True,
                return_numpy=True,
            )
        buf = io.BytesIO()
        np.savez(buf, **{k: v for k, v in output.items() if isinstance(v, np.ndarray)})
        return buf.getvalue()

    fastapi_app = FastAPI(title="Kimodo API")

    class GenerateRequest(BaseModel):
        prompt: str
        num_frames: int = 196

    @fastapi_app.get("/health")
    def health():
        return {"ok": True, "cuda": torch.cuda.is_available()}

    @fastapi_app.post("/generate")
    async def generate(req: GenerateRequest):
        loop = asyncio.get_event_loop()
        try:
            npz_bytes = await loop.run_in_executor(
                None, _generate_npz, req.prompt, req.num_frames
            )
        except Exception as exc:
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=str(exc))
        # Return raw NPZ binary — same as local Docker container behaviour
        return Response(content=npz_bytes, media_type="application/octet-stream")

    return fastapi_app
