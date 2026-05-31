"""Stage 4 — Retopology.

Honest reality for complex characters
--------------------------------------
Automated retopo (Instant Meshes, QuadriFlow, Decimate) does NOT work reliably
on AI-generated characters with overlapping geometry (clothing over body, thin
accessories, fingers). Every automated tool has this limitation.

For complex characters the professional workflow is:
  • Keep the high-poly as a baking source
  • Manually retopo to a low-poly cage in Blender
  • Bake normals/AO high→low

This stage therefore runs in one of two modes, selectable via params["retopo_mode"]:

  "gentle"  (default) — Decimate COLLAPSE at a conservative ratio.
                         Shape is preserved, no holes, no disfiguration.
                         Good for simple props and hard-surface objects.
                         For characters use as a starting point and clean
                         up manually afterward.

  "manual"  — Opens Blender's built-in retopo/sculpt tools and marks the
               stage as MANUAL so the pipeline continues normally.
               Use this for complex organic characters.

Platform face-count presets (from params["platform"]):
  mobile   →  3 000 – 5 000 tris
  indie    →  8 000 – 15 000 tris    ← default
  console  → 20 000 – 40 000 tris
  custom   → use params["target_faces"] directly

NOTE: Instant Meshes and QuadriFlow remain available and are attempted first
when configured, but Gentle Decimate is the reliable default for characters.
"""
from __future__ import annotations

import bmesh
import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState, StageStatus

from assetforge.core.backends.remesh.meshy_remesh import MeshyRemeshBackend, MeshyRemeshError
from assetforge.core.backends._platform import platform_target

from .instant_meshes import InstantMeshesBackend, InstantMeshesError
from .utils import apply_single_modifier, ensure_object, set_active

# Target face counts per platform (triangles)
_PLATFORM_TARGETS = {
    "mobile":  5_000,
    "indie":   12_000,
    "console": 30_000,
}
_DEFAULT_PLATFORM = "indie"


def _target_for(params: dict, faces_before: int) -> int:
    if "target_faces" in params:
        return int(params["target_faces"])
    return platform_target(params.get("platform", _DEFAULT_PLATFORM))


class RetopoBackend(Backend):
    name = "quadriflow"
    stage = "retopo"

    def __init__(self) -> None:
        self._im = InstantMeshesBackend()
        self._mr = MeshyRemeshBackend()

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("retopo", input_types=("mesh",), output_types=("mesh",),
                            emits_quads=True)

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        mode = params.get("retopo_mode", "gentle")

        # ── Manual mode ─────────────────────────────────────────────────
        if mode == "manual":
            state.set_status("retopo", StageStatus.MANUAL)
            state.metadata.setdefault("retopo", {})["method"] = "manual"
            print("[AssetForge] retopo: manual mode — do it in Blender, "
                  "then click ▶ on the next stage to continue.")
            return state

        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object in scene — generate stage must run first")

        set_active(obj)
        faces_before = len(obj.data.polygons)
        target = _target_for(params, faces_before)
        reduction_pct = (1 - target / max(faces_before, 1)) * 100

        print(f"[AssetForge] retopo ({mode}): {faces_before:,} → {target:,} "
              f"({reduction_pct:.0f}% reduction, "
              f"platform={params.get('platform', _DEFAULT_PLATFORM)})")

        # Warn if reduction is very aggressive (>85%)
        if reduction_pct > 85:
            print(f"[AssetForge] retopo WARNING: {reduction_pct:.0f}% reduction is "
                  f"aggressive. Complex characters may show artifacts. "
                  f"Consider retopo_mode='manual' or a higher target.")

        # ── Pre-clean ────────────────────────────────────────────────────
        _pre_clean(obj)

        used = None

        # 1. Meshy Remesh API (best quality — proprietary trained quad remesher)
        mr_ok, mr_reason = self._mr.is_available(ctx, RunMode.API)
        if mr_ok:
            try:
                self._mr.run_api(state, params, ctx)
                # Meshy swapped the GLB on disk; clear cached object + re-import
                state.artifacts.pop("blender_object", None)
                obj = ensure_object(state)
                used = "meshy_remesh"
            except MeshyRemeshError as exc:
                print(f"[AssetForge] Meshy Remesh failed ({exc}) — trying Instant Meshes")
        else:
            print(f"[AssetForge] Meshy Remesh unavailable ({mr_reason})")

        # 2. Instant Meshes (best local quality — field-aligned quads)
        if used is None:
            im_ok, _ = self._im.is_available(ctx, RunMode.LOCAL)
            if im_ok:
                try:
                    self._im.run_local(state, params, ctx)
                    obj = bpy.data.objects.get(obj.name) or obj
                    used = "instant_meshes"
                except InstantMeshesError as exc:
                    print(f"[AssetForge] Instant Meshes failed ({exc}) — trying QuadriFlow")

        # 3. QuadriFlow (built-in, needs viewport context)
        if used is None:
            qf = _try_quadriflow(obj, target)
            if qf and len(obj.data.polygons) != faces_before:
                used = qf

        # 4. Decimate COLLAPSE — always works, shape-preserving
        if used is None:
            _apply_decimate(obj, faces_before, target)
            used = "decimate_collapse"

        # ── Post-repair ──────────────────────────────────────────────────
        holes, loose = _post_repair(obj)
        if holes or loose:
            print(f"[AssetForge] retopo post-repair: {holes} holes filled, "
                  f"{loose} loose verts removed")

        faces_final = len(obj.data.polygons)
        print(f"[AssetForge] retopo via {used}: {faces_before:,} → {faces_final:,}")

        state.artifacts["topology"] = "quad" if used in ("meshy_remesh", "instant_meshes", "quadriflow") else "tri"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {}).update({
            "method": used,
            "platform": params.get("platform", _DEFAULT_PLATFORM),
            "faces_before": faces_before,
            "faces_after": faces_final,
            "holes_filled": holes,
        })
        return state


# ---------------------------------------------------------------------------
# Pre-clean / post-repair
# ---------------------------------------------------------------------------

def _pre_clean(obj) -> None:
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
        print(f"[AssetForge] pre-clean: merged {merged} duplicate verts")


def _post_repair(obj) -> tuple:
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary = [e for e in bm.edges if not e.is_manifold and len(e.link_faces) < 2]
    holes = 0
    if boundary:
        result = bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
        holes = len(result.get("faces", []))
    loose = [v for v in bm.verts if not v.link_faces]
    loose_count = len(loose)
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.normal_update()
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return holes, loose_count


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
