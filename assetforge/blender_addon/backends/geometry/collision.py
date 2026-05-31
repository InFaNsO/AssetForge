"""Stage 11 — Collision shape. Convex hull, visible in scene (Option A).

Naming conventions supported (user-selectable via params):
    unreal  ->  UCX_{base}   (Unreal's static mesh collision import convention)
    unity   ->  {base}_col
    godot   ->  {base}-col
    generic ->  {base}_COL   (default)
"""
from __future__ import annotations

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import apply_modifiers, duplicate_object, ensure_object, set_active

_NAMING = {
    "unreal":  lambda b: f"UCX_{b}",
    "unity":   lambda b: f"{b}_col",
    "godot":   lambda b: f"{b}-col",
    "generic": lambda b: f"{b}_COL",
}


class CollisionBackend(Backend):
    name = "convex_collision"
    stage = "collision"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("collision", input_types=("mesh",), output_types=("mesh",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object for collision generation")

        engine = params.get("engine", "generic").lower()
        namer = _NAMING.get(engine, _NAMING["generic"])
        col_name = namer(obj.name)

        # Remove stale collision object if re-running
        if col_name in bpy.data.objects:
            bpy.data.objects.remove(bpy.data.objects[col_name], do_unlink=True)

        col_obj = duplicate_object(obj, col_name)
        apply_modifiers(col_obj)

        # Build convex hull in Edit mode
        set_active(col_obj)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.convex_hull()
        bpy.ops.object.mode_set(mode="OBJECT")

        # Wireframe display so it doesn't obscure the main mesh
        col_obj.display_type = "WIRE"
        col_obj.hide_render = True

        # Place in a dedicated collection
        _move_to_col_collection(col_obj)

        print(f"[AssetForge] collision: {col_name} (engine={engine})")
        state.artifacts["collision"] = col_name
        state.artifacts["blender_object"] = obj.name
        return state


def _move_to_col_collection(obj) -> None:
    col_name = "AF_Collision"
    col = bpy.data.collections.get(col_name)
    if col is None:
        col = bpy.data.collections.new(col_name)
        bpy.context.scene.collection.children.link(col)
    for c in list(obj.users_collection):
        c.objects.unlink(obj)
    col.objects.link(obj)
