"""Stage 4 — Retopology.

Strategy (in order of preference):
  1. QuadriFlow — best quad topology. Needs a VIEW_3D area for context; we find
     one via ``bpy.context.screen.areas`` and use ``temp_override``. Falls back if
     no viewport is open or the operator errors.
  2. Voxel Remesh — always available, applied via the depsgraph method so no
     operator context is needed. Less topology-aware but reliable.

Why not bpy.ops for modifier apply?
  ``bpy.ops.object.modifier_apply()`` requires a 3D-viewport operator context that
  is not available when called from within another operator (like our run_to_end).
  The depsgraph method (``obj.evaluated_get(depsgraph)`` → ``new_from_object``) works
  from any context and is the recommended approach in Blender 3.2+.
"""
from __future__ import annotations

import math

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import apply_single_modifier, ensure_object, set_active


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

        faces_before = len(obj.data.polygons)
        print(f"[AssetForge] retopo: {faces_before} polys → target {target_faces}")

        used = _try_quadriflow(obj, target_faces)
        faces_after = len(obj.data.polygons)

        # If QuadriFlow didn't run or barely changed the mesh, use Voxel Remesh.
        if used is None or faces_after == faces_before:
            print(f"[AssetForge] retopo: QuadriFlow unavailable — using Voxel Remesh")
            _apply_voxel_remesh(obj, target_faces)
            used = "voxel_remesh"

        faces_final = len(obj.data.polygons)
        print(f"[AssetForge] retopo via {used}: {faces_before} → {faces_final} polys")

        state.artifacts["topology"] = "quad"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {}).update(
            {"method": used, "faces_before": faces_before, "faces_after": faces_final})
        return state


def _try_quadriflow(obj, target_faces: int) -> str | None:
    """Try QuadriFlow with a VIEW_3D temp_override. Returns 'quadriflow' or None."""
    areas = [a for a in bpy.context.screen.areas if a.type == "VIEW_3D"]
    if not areas:
        return None
    try:
        with bpy.context.temp_override(area=areas[0], active_object=obj):
            result = bpy.ops.object.quadriflow_remesh(
                mode="FACES",
                target_faces=target_faces,
                use_preserve_sharp=True,
                use_preserve_boundary=True,
                smooth_normals=False,
            )
        return "quadriflow" if "FINISHED" in result else None
    except Exception as exc:
        print(f"[AssetForge] QuadriFlow failed ({exc}) — will use Voxel Remesh")
        return None


def _apply_voxel_remesh(obj, target_faces: int) -> None:
    """Apply Voxel Remesh using the depsgraph method (no viewport context needed)."""
    dims = obj.dimensions
    obj_size = max(dims.x, dims.y, dims.z, 0.01)
    voxel_size = max(0.002, obj_size / math.sqrt(max(target_faces, 100) / 6))

    mod = obj.modifiers.new("AF_VoxelRemesh", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = voxel_size
    mod.use_smooth_shade = True
    mod.adaptivity = 0.0

    apply_single_modifier(obj, mod)
