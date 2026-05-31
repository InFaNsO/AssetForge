"""Shared bpy helpers used by every geometry backend."""
from __future__ import annotations

import os

import bpy

from assetforge.core.asset_state import AssetState


def ensure_object(state: AssetState):
    """Return the working Blender mesh object, importing from the mesh artifact if needed."""
    name = state.artifacts.get("blender_object")
    if name and name in bpy.data.objects and bpy.data.objects[name].type == "MESH":
        return bpy.data.objects[name]

    mesh_path = state.artifacts.get("mesh")
    if not mesh_path:
        return None
    path = str(mesh_path)
    if not os.path.exists(path):
        return None

    bpy.ops.object.select_all(action="DESELECT")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        return None

    mesh_objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not mesh_objs:
        return None

    obj = max(mesh_objs, key=lambda o: len(o.data.vertices))
    state.artifacts["blender_object"] = obj.name
    return obj


def set_active(obj) -> None:
    """Make *obj* the active and only-selected object in OBJECT mode."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    if bpy.context.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")


def apply_modifiers(obj) -> None:
    """Apply ALL modifiers on *obj* without needing a 3D-viewport operator context.

    Uses the depsgraph evaluation method (Blender 2.90+) which works from any
    Python/operator context — no ``bpy.ops.object.modifier_apply()`` needed.
    """
    if not obj.modifiers:
        return
    set_active(obj)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval  = obj.evaluated_get(depsgraph)
    new_mesh  = bpy.data.meshes.new_from_object(obj_eval)
    old_mesh  = obj.data
    obj.data  = new_mesh
    obj.modifiers.clear()
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def apply_single_modifier(obj, mod) -> None:
    """Apply one modifier and remove it using the depsgraph method."""
    # Evaluate with the modifier active, swap mesh, then remove modifier
    set_active(obj)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval  = obj.evaluated_get(depsgraph)
    new_mesh  = bpy.data.meshes.new_from_object(obj_eval)
    old_mesh  = obj.data
    obj.data  = new_mesh
    obj.modifiers.remove(mod)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)


def duplicate_object(obj, new_name: str):
    """Return a linked-data-free copy of *obj* named *new_name*."""
    set_active(obj)
    bpy.ops.object.duplicate(linked=False)
    dup = bpy.context.active_object
    dup.name = new_name
    dup.data.name = new_name
    return dup
