"""Modal deployment for Kimodo text-to-motion generation.

SETUP (one-time):
  1. Install Modal:        pip install modal
  2. Authenticate:         modal setup          (opens browser)
  3. Create HF secret in the Modal dashboard:
       Secrets -> New -> name it "huggingface" -> add key HF_TOKEN = <your token>
     (Needs read access to gated repos; Llama-3-8B requires HF licence acceptance)
  4. (Optional) If ghcr.io/eyalenav/kimodo-api is private, create a "ghcr" secret:
       key REGISTRY_TOKEN = <GitHub PAT with read:packages>

DEPLOY:
  modal deploy assetforge/modal/kimodo_app.py

  Modal prints a URL like:
    https://<username>--last-rite-kimodo-kimodo-api.modal.run

  Set that as the Kimodo endpoint in AssetForge:
    Windows:  $env:ASSETFORGE_KIMODO_URL = "https://..."
    Or add it to your Blender launch script / system env vars.

API (identical to the local Docker container):
  POST /generate  {"prompt": "character doing a heavy overhead slam"}
                  -> NPZ binary (SOMA skeleton, 77 joints @ 30 fps)
  GET  /health    -> {"status": "ok"}

COLD START:
  First call downloads ~16 GB of weights (Llama-3-8B + Kimodo).
  The modal.Volume mount caches them so subsequent cold starts are fast.
  Expect ~3-5 min on first call; ~30 s on warm/cached calls.
"""

import modal

# ── Image ─────────────────────────────────────────────────────────────────────
# ghcr.io/eyalenav/kimodo-api has all Kimodo + uvicorn deps pre-installed.
# TEXT_ENCODER_DEVICE=cpu offloads the 13 GB Llama-3-8B text encoder to RAM,
# leaving the full 24 GB A10G for the motion diffusion model.
image = (
    modal.Image.from_registry("ghcr.io/eyalenav/kimodo-api:latest")
    .env({"TEXT_ENCODER_DEVICE": "cpu"})
)

# ── Weight cache ──────────────────────────────────────────────────────────────
# HuggingFace downloads land in ~/.cache/huggingface by default.
# Mounting a persistent Volume there means the 16 GB download happens once,
# not on every cold start.
weights_vol = modal.Volume.from_name("kimodo-weights", create_if_missing=True)

# ── App ───────────────────────────────────────────────────────────────────────
app = modal.App("last-rite-kimodo")


@app.function(
    image=image,
    gpu="A10G",                         # 24 GB VRAM — ample with text encoder on CPU
    timeout=900,                        # 15 min ceiling for long/complex prompts
    concurrency_limit=1,                # one generation per container
    container_idle_timeout=600,         # keep warm 10 min between AssetForge calls
    volumes={"/root/.cache": weights_vol},
    secrets=[
        modal.Secret.from_name("huggingface"),  # exposes HF_TOKEN env var
    ],
)
@modal.asgi_app()
def kimodo_api():
    """ASGI entry point — returns the Kimodo FastAPI app instance.

    Modal routes all HTTPS traffic to this app.  The app exposes the same
    /generate and /health routes as the local Docker container, so the
    KimodoBackend in assetforge/core/backends/kimodo/kimodo.py works
    unchanged with any URL (localhost or Modal HTTPS).
    """
    from kimodo.scripts.run_motion_api import build_app   # type: ignore[import]
    return build_app()
