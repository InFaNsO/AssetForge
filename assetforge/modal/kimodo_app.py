"""Modal deployment for Kimodo text-to-motion generation.

SETUP (one-time):
  1. Install Modal:   pip install modal
  2. Authenticate:    modal setup        (opens browser — one time only)

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

NOTE — HuggingFace token:
  The kimodo-api image works without HF_TOKEN (weights are bundled / pulled
  from non-gated sources).  If a future image version requires a token,
  create a Modal secret named "huggingface" (key: HF_TOKEN) and add:
    secrets=[modal.Secret.from_name("huggingface")]
  to the @app.function decorator below.

COLD START:
  Any runtime weight download is cached in the mounted Volume so subsequent
  cold starts are fast.  First call may take 3-5 min; warm calls ~30 s.
"""

import modal

# ── Image ─────────────────────────────────────────────────────────────────────
# ghcr.io/eyalenav/kimodo-api has all Kimodo + uvicorn deps pre-installed.
# TEXT_ENCODER_DEVICE=cpu offloads the text encoder to RAM, leaving the full
# A10G VRAM for the motion diffusion model.
image = (
    modal.Image.from_registry("ghcr.io/eyalenav/kimodo-api:latest")
    .env({"TEXT_ENCODER_DEVICE": "cpu"})
)

# ── Weight cache ──────────────────────────────────────────────────────────────
# Any runtime downloads land in ~/.cache; the Volume keeps them across cold starts.
weights_vol = modal.Volume.from_name("kimodo-weights", create_if_missing=True)

# ── App ───────────────────────────────────────────────────────────────────────
app = modal.App("last-rite-kimodo")


@app.function(
    image=image,
    gpu="A10G",                         # 24 GB VRAM
    timeout=900,                        # 15 min ceiling for complex prompts
    max_containers=1,                   # one generation at a time
    scaledown_window=600,               # keep warm 10 min between AssetForge calls
    volumes={"/root/.cache": weights_vol},
)
@modal.asgi_app()
def kimodo_api():
    """ASGI entry point — returns the Kimodo FastAPI app instance.

    Modal routes all HTTPS traffic here.  Same /generate and /health routes
    as the local Docker container, so KimodoBackend works with any URL.
    """
    from kimodo.scripts.run_motion_api import build_app   # type: ignore[import]
    return build_app()
