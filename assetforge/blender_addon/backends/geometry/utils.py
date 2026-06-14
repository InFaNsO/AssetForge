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
        fix_armature_bone_display(bpy.context.selected_objects)
    else:
        return None

    mesh_objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]
    if not mesh_objs:
        return None

    obj = max(mesh_objs, key=lambda o: len(o.data.vertices))
    state.artifacts["blender_object"] = obj.name
    return obj


def fix_armature_bone_display(objects=None) -> int:
    """Repair the absurd bone tail lengths Blender's glTF importer assigns to
    Meshy/Mixamo rigs.

    glTF stores no bone tails, so the importer guesses them — for Meshy rigs it picks
    lengths 10-40x the body size, drawing the skeleton as a spiky star (and breaking
    viewport framing). For each affected armature we shorten every bone ALONG ITS
    CURRENT DIRECTION to reach its nearest child joint. Bone heads, directions and
    roll are untouched, so the bind/skinning and any animation are byte-for-byte
    identical — only the on-screen bone length changes. Idempotent: a no-op on
    healthy rigs (and on already-fixed ones), so it is safe to call after every import.

    Args:
        objects: objects to check (e.g. the freshly imported ones); None -> whole scene.
    Returns: number of bones adjusted.
    """
    pool = list(objects) if objects is not None else list(bpy.data.objects)
    total = 0
    for arm in [o for o in pool if getattr(o, "type", None) == "ARMATURE"]:
        bones = arm.data.bones
        if len(bones) < 2:
            continue
        mw = arm.matrix_world
        heads = [mw @ b.head_local for b in bones]
        extent = max((h - g).length for h in heads for g in heads)
        max_len = max((mw @ b.tail_local - mw @ b.head_local).length for b in bones)
        # only act when clearly degenerate: a single bone longer than the whole skeleton
        if extent <= 1e-6 or max_len <= extent * 1.5:
            continue
        total += _shorten_bones_to_children(arm)
    return total


def _shorten_bones_to_children(arm) -> int:
    """Edit-mode pass: shorten each bone to its nearest child, keeping its direction.
    Works both from the UI and headless (MCP) via a VIEW_3D context override, and
    restores the prior selection / active object. Returns the count of bones adjusted."""
    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active
    prev_selected = [o for o in view_layer.objects if o.select_get()]

    def _edit():
        ebs = arm.data.edit_bones
        n = 0
        for eb in ebs:                       # pass 1: bones with children -> reach child
            kids = eb.children
            if not kids:
                continue
            vec = eb.tail - eb.head
            if vec.length < 1e-9:
                continue
            L = min((c.head - eb.head).length for c in kids)
            if L < 1e-4:
                continue
            eb.tail = eb.head + vec.normalized() * L
            n += 1
        for eb in ebs:                       # pass 2: leaf bones -> short stub off parent
            if eb.children:
                continue
            vec = eb.tail - eb.head
            if vec.length < 1e-9:
                continue
            plen = eb.parent.length if (eb.parent and eb.parent.length > 1e-4) else 0.0
            L = plen * 0.5 if plen > 1e-4 else max(0.01, vec.length * 0.02)
            eb.tail = eb.head + vec.normalized() * L
            n += 1
        return n

    bpy.ops.object.select_all(action="DESELECT")
    arm.select_set(True)
    view_layer.objects.active = arm

    n = 0
    try:
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.mode_set(mode="EDIT")
        n = _edit()
        bpy.ops.object.mode_set(mode="OBJECT")
    except RuntimeError:
        # headless / missing active-object context: retry under a VIEW_3D override
        area = next((a for w in bpy.context.window_manager.windows
                     for a in w.screen.areas if a.type == "VIEW_3D"), None)
        if area is None:
            return 0
        win = next(w for w in bpy.context.window_manager.windows
                   if any(a == area for a in w.screen.areas))
        region = next(r for r in area.regions if r.type == "WINDOW")
        with bpy.context.temp_override(window=win, screen=win.screen, area=area,
                                       region=region, active_object=arm,
                                       selected_objects=[arm],
                                       selected_editable_objects=[arm], object=arm):
            bpy.ops.object.mode_set(mode="EDIT")
            n = _edit()
            bpy.ops.object.mode_set(mode="OBJECT")

    # restore prior selection / active object
    try:
        bpy.ops.object.select_all(action="DESELECT")
        for o in prev_selected:
            o.select_set(True)
        view_layer.objects.active = prev_active
    except Exception:
        pass
    return n


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
