"""Stub backends — one per ML/backed stage so the whole chain runs before any real model
lands (DEVELOPMENT_PLAN.md §3: "Every stage works with a stub before a real backend").

Each stub mutates the asset state plausibly (sets the artifact a real backend would
produce) and returns it. Deterministic, no I/O, no GPU. The generation stub deliberately
declares all three run modes so the resolver/automation path is exercised end-to-end.

`build_stub_registry()` returns a registry wired for the full v1 stage set, mirroring the
Phase-1 "minimum backends per stage" table (DEVELOPMENT_PLAN.md §2.5):
generation -> Copilot 3D (automation) + one API stub; everything else -> one algo/stub.
"""
from __future__ import annotations

from typing import Optional

from ..adapter import (
    Backend, BackendRegistry, Capabilities, CostEstimate, RunContext, RunMode,
)
from ..asset_state import AssetState


class _StubBase(Backend):
    """Marks a stage done by writing its expected artifact key."""

    artifact_key: str = ""
    artifact_value = True

    def capabilities(self) -> Capabilities:
        return Capabilities(stage=self.stage)

    def _apply(self, state: AssetState) -> AssetState:
        if self.artifact_key:
            state.artifacts[self.artifact_key] = self.artifact_value
        return state


# --- Stage 3: Generation. Free automation (Copilot 3D) + paid API stub. ---
class Copilot3DStub(_StubBase):
    """Stands in for Microsoft Copilot 3D: free, browser-automated, image -> GLB."""

    name = "copilot3d"
    stage = "generate"
    artifact_key = "mesh"

    def supports_automation(self) -> bool:
        return True

    def is_available(self, ctx: RunContext, mode: RunMode):
        # Free web app: no key, but needs a signed-in session. Stub: always available.
        return True, "free (browser session)"

    def capabilities(self) -> Capabilities:
        return Capabilities("generate", input_types=("image",), output_types=("mesh",),
                            emits_quads=False)

    def cost_estimate(self, state, params) -> CostEstimate:
        return CostEstimate(seconds=60.0, credits=0.0)

    def run_automation(self, state, params, ctx) -> AssetState:
        state.artifacts["mesh"] = "work/stub/copilot3d_mesh.glb"
        return state


class GenApiStub(_StubBase):
    """Stands in for a paid generation API (Tripo/Meshy/Rodin)."""

    name = "gen_api"
    stage = "generate"
    secret_name = "tripo"

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("generate", input_types=("image", "text"),
                            output_types=("mesh",), emits_quads=True)

    def cost_estimate(self, state, params) -> CostEstimate:
        return CostEstimate(seconds=30.0, credits=5.0)

    def run_api(self, state, params, ctx) -> AssetState:
        state.artifacts["mesh"] = "work/stub/gen_api_mesh.glb"
        return state


# --- Generic single-mode stubs for the remaining stages ---
def _algo_stub(stage_key: str, artifact_key: str, value=True):
    class _Algo(_StubBase):
        name = f"{stage_key}_stub"
        stage = stage_key

        def supports_local(self) -> bool:  # algo runs locally, no model/VRAM
            return True

        def vram_required(self) -> Optional[int]:
            return None

        def run_local(self, state, params, ctx) -> AssetState:
            if artifact_key:
                state.artifacts[artifact_key] = value
            return state

    _Algo.artifact_key = artifact_key
    _Algo.__name__ = f"{stage_key.capitalize()}Stub"
    return _Algo


RetopoStub = _algo_stub("retopo", "topology", "quad")
UVStub = _algo_stub("uv", "uv", True)
BakeStub = _algo_stub("bake", "bakes", ("normal", "ao", "curvature"))
TextureStub = _algo_stub("texture", "textures", {"basecolor": True})
RigStub = _algo_stub("rig", "skeleton", "mixamo")
AnimateStub = _algo_stub("animate", "animations", ("idle",))
LodStub = _algo_stub("lod", "lods", (0, 1, 2))
CollisionStub = _algo_stub("collision", "collision", "convex")
ExportStub = _algo_stub("export", "exported", "work/stub/asset.glb")
ValidateStub = _algo_stub("validate", "validation", "passed")


def build_stub_registry() -> BackendRegistry:
    """A registry with a stub for every v1 stage (spec §3 core focus)."""
    reg = BackendRegistry()
    reg.register(Copilot3DStub())
    reg.register(GenApiStub())
    for cls in (RetopoStub, UVStub, BakeStub, TextureStub, RigStub,
                AnimateStub, LodStub, CollisionStub, ExportStub, ValidateStub):
        reg.register(cls())
    return reg
