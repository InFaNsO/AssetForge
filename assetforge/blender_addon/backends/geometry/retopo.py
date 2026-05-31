"""Stage 4 — Retopology. Tries QuadriFlow first; falls back to Voxel Remesh."""
from __future__ import annotations

import math

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import ensure_object, set_active


class RetopoBackend(Backend):
    name = "quadriflow"
    stage = "retopo"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("retopo", input_types=("mesh",), output_types=("mesh",),
                            emits_quads=True)

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object in scene — generate stage must run first")

        target_faces = int(params.get("target_faces", 5_000))
        set_active(obj)

        used = "quadriflow"
        try:
            bpy.ops.object.quadriflow_remesh(
                mode="FACES",
                target_faces=target_faces,
                use_preserve_sharp=True,
                use_preserve_boundary=True,
                smooth_normals=False,
            )
        except Exception:
            # Fallback: Voxel Remesh modifier (always available, less topology-aware)
            used = "voxel_remesh"
            mod = obj.modifiers.new("AF_Remesh", "REMESH")
            mod.mode = "VOXEL"
            dims = obj.dimensions
            obj_size = max(dims.x, dims.y, dims.z, 0.1)
            mod.voxel_size = max(0.005, obj_size / math.sqrt(target_faces / 6))
            mod.adaptivity = 0.0
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.modifier_apply(modifier=mod.name)

        print(f"[AssetForge] retopo via {used}: target_faces={target_faces}")
        state.artifacts["topology"] = "quad"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {})["method"] = used
        return state
