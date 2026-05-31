"""Stage 10 — LOD chain. Creates 3 levels of detail as separate scene objects (Option A).

Naming convention (matches Unreal Engine LOD import convention):
    {base}       — original (LOD0 — full detail)
    {base}_LOD1  — 50 % faces
    {base}_LOD2  — 25 % faces
    {base}_LOD3  — 10 % faces

All modifier applications use the depsgraph method (no 3D-viewport context needed).
"""
from __future__ import annotations

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import apply_modifiers, apply_single_modifier, duplicate_object, ensure_object, set_active

_DEFAULT_RATIOS = [0.5, 0.25, 0.10]


class LODBackend(Backend):
    name = "decimate_lod"
    stage = "lod"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("lod", input_types=("mesh",), output_types=("mesh",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object for LOD generation")

        ratios: list[float] = list(params.get("ratios", _DEFAULT_RATIOS))
        base_name = obj.name
        base_faces = len(obj.data.polygons)
        lod_names: list[str] = []

        for i, ratio in enumerate(ratios, start=1):
            lod_name = f"{base_name}_LOD{i}"
            if lod_name in bpy.data.objects:
                bpy.data.objects.remove(bpy.data.objects[lod_name], do_unlink=True)

            dup = duplicate_object(obj, lod_name)
            # Flatten any existing modifiers first, then add Decimate
            apply_modifiers(dup)

            mod = dup.modifiers.new("AF_Decimate", "DECIMATE")
            mod.decimate_type = "COLLAPSE"
            mod.ratio = ratio
            apply_single_modifier(dup, mod)   # depsgraph — no viewport context needed

            _move_to_lod_collection(dup)
            lod_faces = len(dup.data.polygons)
            print(f"[AssetForge] LOD{i}: {lod_name}  {base_faces} → {lod_faces} polys ({ratio*100:.0f}%)")
            lod_names.append(lod_name)

        set_active(obj)
        state.artifacts["lods"] = lod_names
        state.artifacts["blender_object"] = base_name
        return state


def _move_to_lod_collection(obj) -> None:
    col_name = "AF_LODs"
    col = bpy.data.collections.get(col_name)
    if col is None:
        col = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(col)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)
