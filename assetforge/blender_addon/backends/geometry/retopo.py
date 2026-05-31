"""Stage 4 — Retopology.

Strategy (in order of preference):
  1. Instant Meshes  — best quality. Field-aligned quad remesher that follows
                       surface curvature. Free CLI tool; set path in preferences.
  2. QuadriFlow      — built-in quad remesh. Needs a VIEW_3D area (temp_override).
                       Works best on watertight meshes.
  3. Decimate COLLAPSE — shape-preserving fallback. Reduces poly count while
                         following the surface. Clean results at gentle ratios
                         (≤50 % reduction); shows artifacts at aggressive ratios.

Every method is wrapped with:
  • _pre_clean()   — merge duplicate verts, dissolve degenerate faces (bmesh,
                     context-independent). Removes the bad topology that causes
                     holes and intersection artefacts in generated meshes.
  • _post_repair() — fill holes created by the remesher, remove loose verts
                     (bmesh, context-independent). Fixes the "openings" that
                     automated tools produce on multi-piece meshes.

Target face count
  The default is RELATIVE: keep 40 % of the original faces, with a floor of
  20 000 and a ceiling of 80 000.  This avoids the 90 %+ reductions that
  create visible artefacts on complex characters.  Pass ``target_faces`` in
  params to override with an explicit count.

⚠ Voxel Remesh is NOT used — it voxelises the volume and destroys thin parts.
"""
from __future__ import annotations

import bmesh
import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .instant_meshes import InstantMeshesBackend, InstantMeshesError
from .utils import apply_single_modifier, ensure_object, set_active


def _default_target(faces_before: int) -> int:
    """Relative target: 40 % of input, clamped 20 k–80 k."""
    return max(20_000, min(80_000, int(faces_before * 0.4)))


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

        set_active(obj)
        faces_before = len(obj.data.polygons)
        target_faces = int(params.get("target_faces", _default_target(faces_before)))

        print(f"[AssetForge] retopo: {faces_before} polys → target {target_faces} "
              f"({target_faces/max(faces_before,1)*100:.0f}% of original)")

        # ── Pre-clean (always, before any remesh) ────────────────────────
        _pre_clean(obj)

        used = None

        # 1. Instant Meshes
        im_ok, _ = self._im.is_available(ctx, RunMode.LOCAL)
        if im_ok:
            try:
                self._im.run_local(state, params, ctx)
                # run_local may have swapped obj.data; re-fetch
                obj = bpy.data.objects.get(obj.name) or obj
                used = "instant_meshes"
            except InstantMeshesError as exc:
                print(f"[AssetForge] Instant Meshes failed ({exc}) — trying QuadriFlow")

        # 2. QuadriFlow
        if used is None:
            qf = _try_quadriflow(obj, target_faces)
            if qf and len(obj.data.polygons) != faces_before:
                used = qf

        # 3. Decimate COLLAPSE
        if used is None:
            print("[AssetForge] retopo: using Decimate COLLAPSE")
            _apply_decimate(obj, faces_before, target_faces)
            used = "decimate_collapse"

        # ── Post-repair (always, after any remesh) ────────────────────────
        holes_filled, loose_removed = _post_repair(obj)
        if holes_filled or loose_removed:
            print(f"[AssetForge] retopo post-repair: "
                  f"filled {holes_filled} holes, removed {loose_removed} loose verts")

        faces_final = len(obj.data.polygons)
        print(f"[AssetForge] retopo via {used}: {faces_before} → {faces_final} polys")

        state.artifacts["topology"] = "quad" if used in ("instant_meshes", "quadriflow") else "tri"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {}).update({
            "method": used,
            "faces_before": faces_before,
            "faces_after": faces_final,
            "holes_filled": holes_filled,
        })
        return state


# ---------------------------------------------------------------------------
# Pre-clean
# ---------------------------------------------------------------------------

def _pre_clean(obj) -> None:
    """Merge duplicate verts and dissolve degenerate faces using bmesh.

    AI-generated meshes often have many duplicate vertices at surface seams and
    zero-area faces from triangulation artefacts. Both cause holes and bad normals
    after remeshing.  This runs before any retopo method.
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    verts_before = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.dissolve_degenerate(bm, dist=0.0001, edges=bm.edges)
    bm.normal_update()

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    merged = verts_before - len(obj.data.vertices)
    if merged:
        print(f"[AssetForge] retopo pre-clean: merged {merged} duplicate verts")


# ---------------------------------------------------------------------------
# Post-repair
# ---------------------------------------------------------------------------

def _post_repair(obj) -> tuple:
    """Fill holes and remove loose geometry using bmesh after any retopo method.

    Remeshers (including Instant Meshes) often leave open boundary edges on
    multi-piece meshes. ``holes_fill`` closes these; ``delete VERTS`` removes
    any stray vertices that are no longer connected to faces.

    Returns (holes_filled, loose_verts_removed).
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    # Boundary edges = edges that belong to only one face = hole perimeters
    boundary = [e for e in bm.edges if not e.is_manifold and len(e.link_faces) < 2]
    holes_filled = 0
    if boundary:
        result = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
        holes_filled = len(result.get("faces", []))

    # Loose vertices (not connected to any face)
    loose = [v for v in bm.verts if not v.link_faces]
    loose_removed = len(loose)
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    bm.normal_update()
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    return holes_filled, loose_removed


# ---------------------------------------------------------------------------
# Remesh methods
# ---------------------------------------------------------------------------

def _try_quadriflow(obj, target_faces: int):
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
    ratio = max(0.01, min(0.99, target_faces / max(faces_before, 1)))
    mod = obj.modifiers.new("AF_Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    mod.use_collapse_triangulate = False
    apply_single_modifier(obj, mod)
