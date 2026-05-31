"""Copilot 3D generation backend (free, browser-driven).

The adapter is thin: it delegates the actual browser work to an injected
:class:`BrowserDriver` (drivers.py). That keeps the brittle automation isolated and lets
us start with ManualDriver today and swap to Playwright / MCP later with no API change.
"""
from __future__ import annotations

from typing import Optional

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState
from .drivers import BrowserDriver, GenerationPending, ManualDriver


class Copilot3DBackend(Backend):
    name = "copilot3d"
    stage = "generate"
    secret_name = None  # free: no API key (needs a signed-in browser session instead)

    def __init__(self, driver: Optional[BrowserDriver] = None) -> None:
        self.driver = driver or ManualDriver()

    def supports_automation(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("generate", input_types=("image",), output_types=("mesh",),
                            emits_quads=False)

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=90.0, credits=0.0)

    def is_available(self, ctx: RunContext, mode: RunMode):
        # Free; availability depends on the driver (a signed-in session / manual input),
        # which is reported at run time rather than blocking resolution here.
        return True, "free (browser session)"

    def run_automation(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        # When downloaded_glb is provided the driver ignores image_path entirely —
        # no source_kind check needed. The check only applies to live browser generation.
        if not params.get("downloaded_glb") and state.source_kind.value != "image":
            raise GenerationPending("Copilot 3D needs an image input (source_kind=image).")
        driver_params = dict(params)
        driver_params.setdefault("asset_id", state.id)
        glb = self.driver.generate(state.source_ref, ctx.work_dir, driver_params)
        state.artifacts["mesh"] = glb
        state.metadata.setdefault("generation", {})["backend"] = self.name
        return state
