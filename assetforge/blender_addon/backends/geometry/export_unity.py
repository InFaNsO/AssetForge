"""Stage 12 (Unity variant) — exports a rigged character as FBX for Unity Humanoid.

Handles:
  - Meshy armature scale artifact (0.01 cm-space): applies scale in-place before export
    so bone positions read correctly in Unity (1.7 m character, not 0.017 m).
  - Unity coordinate system (Y-up, -Z forward via axis_forward/axis_up params).
  - All Blender actions baked into FBX animation clips.
  - Armature + all skinned mesh objects exported together.
  - Optional texture embedding (embed_textures=True copies PBR maps into the FBX).

Output: {state.id}_unity.fbx in ctx.work_dir.
state.artifacts["exported_unity"] is set to the absolute path.
"""
from __future__ import annotations

import os
from typing import Optional

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import set_active


class UnityFBXExportBackend(Backend):
    name = "unity_fbx_export"
    stage = "export"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("export",
                            input_types=("mesh", "skeleton"),
                            output_types=("file",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        armature_name: Optional[str] = params.get("armature_name")
        embed_tex: bool = bool(params.get("embed_textures", False))

        armature, meshes = _collect_objects(state, armature_name)
        if not armature and not meshes:
            raise RuntimeError(
                "No armature or mesh found in scene. "
                "Call import_mesh() or import the rigged GLB first."
            )

        out_path = os.path.join(ctx.work_dir, f"{state.id}_unity.fbx")
        os.makedirs(ctx.work_dir, exist_ok=True)

        # ── Fix Meshy 0.01 armature scale ────────────────────────────────────
        # Meshy rigged GLBs export with armature.scale = (0.01, 0.01, 0.01) and
        # bone positions in cm-space. Applying scale makes world-space positions
        # match real-world units (bones read as 1.7 m tall, not 0.017 m).
        if armature is not None and _needs_scale_apply(armature):
            bpy.ops.object.select_all(action="DESELECT")
            armature.select_set(True)
            bpy.context.view_layer.objects.active = armature
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
            bpy.ops.object.transform_apply(
                location=False, rotation=False, scale=True)
            print(f"[AssetForge] Unity export: applied armature scale "
                  f"(was {armature.scale[:]!r})")

        # ── Select objects for export ─────────────────────────────────────────
        bpy.ops.object.select_all(action="DESELECT")
        export_objs = ([armature] if armature else []) + meshes
        for obj in export_objs:
            obj.select_set(True)
        active = armature if armature else meshes[0]
        bpy.context.view_layer.objects.active = active

        # ── FBX export ────────────────────────────────────────────────────────
        bpy.ops.export_scene.fbx(
            filepath=out_path,
            use_selection=True,
            # Unit / scale: we already applied armature scale above, so
            # FBX_SCALE_NONE preserves the now-correct transforms as-is.
            apply_unit_scale=True,
            apply_scale_options="FBX_SCALE_NONE",
            bake_space_transform=False,
            # Object types
            object_types={"ARMATURE", "MESH"},
            use_mesh_modifiers=True,
            # Skeleton / rig
            use_armature_deform_only=True,
            add_leaf_bones=False,       # Unity Humanoid doesn't need leaf bones
            primary_bone_axis="Y",      # Unity expects Y-up bones
            secondary_bone_axis="X",
            # Coordinate system: Unity Y-up, -Z forward
            axis_forward="-Z",
            axis_up="Y",
            # Animations
            bake_anim=True,
            bake_anim_use_all_actions=True,
            bake_anim_force_startend_keying=True,
            bake_anim_simplify_factor=1.0,
            # Textures
            path_mode="COPY" if embed_tex else "AUTO",
            embed_textures=embed_tex,
        )

        state.artifacts["exported_unity"] = out_path
        state.metadata.setdefault("export", {})["unity_fbx"] = out_path
        print(f"[AssetForge] Unity FBX exported -> {out_path}")
        return state


# ── helpers ───────────────────────────────────────────────────────────────────

def _needs_scale_apply(armature) -> bool:
    """True when any scale component differs from 1.0 by more than floating-point noise."""
    return any(abs(s - 1.0) > 1e-4 for s in armature.scale)


def _collect_objects(state: AssetState, armature_name: Optional[str] = None):
    """Return (armature_obj | None, list[mesh_obj]).

    Priority:
      1. armature_name param (explicit override)
      2. state.artifacts["blender_object"] + parent armature
      3. Scene scan — armature with the most children, plus all skinned meshes
    """
    armature = None

    # 1. Explicit name override
    if armature_name:
        armature = bpy.data.objects.get(armature_name)

    # 2. Known blender_object → climb to parent armature
    if armature is None:
        bo_name = state.artifacts.get("blender_object")
        if bo_name:
            bo = bpy.data.objects.get(bo_name)
            if bo and bo.parent and bo.parent.type == "ARMATURE":
                armature = bo.parent

    # 3. Scene scan — prefer armature with most children (most likely the char rig)
    if armature is None:
        candidates = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if candidates:
            armature = max(candidates, key=lambda o: len(o.children))

    # Collect meshes: direct children + any mesh with an armature modifier pointing here
    meshes: list = []
    if armature is not None:
        for child in armature.children:
            if child.type == "MESH":
                meshes.append(child)
        for obj in bpy.data.objects:
            if obj.type == "MESH" and obj not in meshes:
                for mod in obj.modifiers:
                    if mod.type == "ARMATURE" and mod.object == armature:
                        meshes.append(obj)
    else:
        meshes = [o for o in bpy.data.objects if o.type == "MESH"]

    return armature, meshes
