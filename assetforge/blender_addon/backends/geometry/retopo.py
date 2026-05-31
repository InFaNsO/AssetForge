"""Stage 4 — Retopology.

Strategy (in order of preference):
  1. Instant Meshes  — best quality. Field-aligned quad remesher that follows
                       surface curvature. Free CLI tool, set path in preferences.
                       Download: instant-meshes-windows.zip from GitHub releases.
  2. QuadriFlow      — built-in, good quad quality. Requires a VIEW_3D area
                       (temp_override). Works best on watertight meshes.
  3. Decimate COLLAPSE — shape-preserving fallback. Collapses edges while
                         following the surface. Keeps proportions but may produce
                         artifacts at high reduction ratios.

⚠ Voxel Remesh is NOT used — it voxelises the volume and reconstructs the
  surface, which merges thin parts and destroys character proportions.

All modifier applications use the depsgraph method (no viewport context needed).
"""
from __future__ import annotations

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .instant_meshes import InstantMeshesBackend, InstantMeshesError
from .utils import apply_single_modifier, ensure_object, set_active

_DEFAULT_FACES = 15_000   # raised from 5k — better quality for characters


class RetopoBackend(Backend):
    name = "quadriflow"
    stage = "retopo"

    def __init__(self) -> None:
        self._im = InstantMeshesBackend()

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("retopo", input_types=("mesh",), output_types=("mesh",),
                            emits_quads=True)

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object in scene — generate stage must run first")

        target_faces = int(params.get("target_faces", _DEFAULT_FACES))
        set_active(obj)
        faces_before = len(obj.data.polygons)
        print(f"[AssetForge] retopo: {faces_before} polys → target {target_faces}")

        used = None

        # 1. Instant Meshes (best quality — field-aligned quads)
        im_ok, im_reason = self._im.is_available(ctx, RunMode.LOCAL)
        if im_ok:
            try:
                self._im.run_local(state, params, ctx)
                used = "instant_meshes"
            except InstantMeshesError as exc:
                print(f"[AssetForge] Instant Meshes failed ({exc}) — trying QuadriFlow")

        # 2. QuadriFlow (built-in, needs viewport context)
        if used is None:
            used = _try_quadriflow(obj, target_faces)
            if used is None or len(obj.data.polygons) == faces_before:
                used = None   # didn't change anything

        # 3. Decimate COLLAPSE (shape-preserving, always works)
        if used is None:
            print("[AssetForge] retopo: using Decimate COLLAPSE (shape-preserving fallback)")
            _apply_decimate(obj, faces_before, target_faces)
            used = "decimate_collapse"

        faces_final = len(obj.data.polygons)
        print(f"[AssetForge] retopo via {used}: {faces_before} → {faces_final} polys")

        state.artifacts["topology"] = "quad" if used in ("instant_meshes", "quadriflow") else "tri"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {}).update(
            {"method": used, "faces_before": faces_before, "faces_after": faces_final})
        return state


def _try_quadriflow(obj, target_faces: int):
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
        print(f"[AssetForge] QuadriFlow failed ({exc})")
        return None


def _apply_decimate(obj, faces_before: int, target_faces: int) -> None:
    """Reduce poly count via Decimate COLLAPSE — shape-preserving, no context needed."""
    ratio = max(0.01, min(0.99, target_faces / max(faces_before, 1)))
    mod = obj.modifiers.new("AF_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = False
    apply_single_modifier(obj, mod)
