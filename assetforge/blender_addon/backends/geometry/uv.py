"""Stage 5 — UV unwrap. Smart UV Project on all faces, then pack islands."""
from __future__ import annotations

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import ensure_object, set_active


class UVBackend(Backend):
    name = "smart_uv"
    stage = "uv"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("uv", input_types=("mesh",), output_types=("mesh",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object for UV unwrap")
        if obj.type != "MESH":
            raise RuntimeError(f"Expected MESH, got {obj.type}")

        angle_limit  = float(params.get("angle_limit", 66.0))
        island_margin = float(params.get("island_margin", 0.02))

        set_active(obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(
            angle_limit=angle_limit,
            island_margin=island_margin,
            correct_aspect=True,
            scale_to_bounds=False,
        )
        # Pack all islands into the 0-1 space
        bpy.ops.uv.pack_islands(margin=island_margin)
        bpy.ops.object.mode_set(mode="OBJECT")

        print(f"[AssetForge] UV unwrap done (angle={angle_limit}°, margin={island_margin})")
        state.artifacts["uv"] = True
        state.artifacts["blender_object"] = obj.name
        return state
