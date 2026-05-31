"""Stage 6 — Baking. Bakes Normal + AO maps using Cycles.

This stage is best-effort: if Cycles is unavailable, or the object has no UV layer, it
records a warning and passes through rather than failing the chain (baking is an
enhancement; it does not block export).
"""
from __future__ import annotations

import os

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import ensure_object, set_active

_BAKE_SIZE = 1024   # pixels — keep fast for Phase 2


def _ensure_material(obj) -> bpy.types.Material:
    """Return the first material slot, creating a default one if needed."""
    if not obj.data.materials or not obj.data.materials[0]:
        mat = bpy.data.materials.new(name=f"{obj.name}_AF")
        mat.use_nodes = True
        if obj.data.materials:
            obj.data.materials[0] = mat
        else:
            obj.data.materials.append(mat)
    mat = obj.data.materials[0]
    mat.use_nodes = True
    return mat


def _setup_bake_node(mat, img) -> bpy.types.ShaderNodeTexImage:
    """Add (or reuse) an Image Texture node set to *img* and make it active."""
    nodes = mat.node_tree.nodes
    node = nodes.get("AF_BakeTarget")
    if node is None:
        node = nodes.new("ShaderNodeTexImage")
        node.name = "AF_BakeTarget"
    node.image = img
    # Make it the active (selected) node so Blender knows where to bake into
    for n in nodes:
        n.select = False
    node.select = True
    mat.node_tree.nodes.active = node
    return node


def _bake_pass(bake_type: str, obj, img_path: str, size: int) -> str | None:
    """Bake *bake_type* ('NORMAL' or 'AO') onto a new image and save to *img_path*."""
    img = bpy.data.images.new(
        name=os.path.basename(img_path), width=size, height=size, alpha=False)
    mat = _ensure_material(obj)
    _setup_bake_node(mat, img)

    # Cycles required for baking
    prev_engine = bpy.context.scene.render.engine
    bpy.context.scene.render.engine = "CYCLES"

    # Use GPU if available to avoid very long bake times
    prefs = bpy.context.preferences.addons.get("cycles")
    if prefs:
        cprefs = prefs.preferences
        if hasattr(cprefs, "compute_device_type"):
            try:
                cprefs.compute_device_type = "CUDA"
            except Exception:
                pass

    set_active(obj)
    try:
        bpy.ops.object.bake(
            type=bake_type,
            use_selected_to_active=False,
            use_clear=True,
            margin=8,
        )
    except Exception as exc:
        bpy.data.images.remove(img)
        bpy.context.scene.render.engine = prev_engine
        print(f"[AssetForge] bake {bake_type} failed: {exc}")
        return None

    img.filepath_raw = img_path
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    bpy.context.scene.render.engine = prev_engine
    print(f"[AssetForge] baked {bake_type} -> {img_path}")
    return img_path


class BakeBackend(Backend):
    name = "cycles_bake"
    stage = "bake"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("bake", input_types=("mesh",), output_types=("textures",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            print("[AssetForge] bake: no mesh — skipping")
            state.artifacts["bakes"] = {}
            return state

        if not state.artifacts.get("uv"):
            print("[AssetForge] bake: no UV layer — skipping (run UV stage first)")
            state.artifacts["bakes"] = {}
            return state

        if "cycles" not in bpy.context.preferences.addons:
            print("[AssetForge] bake: Cycles not enabled — skipping")
            state.artifacts["bakes"] = {}
            return state

        size = int(params.get("resolution", _BAKE_SIZE))
        bakes: dict[str, str] = {}

        for bake_type, suffix in [("NORMAL", "normal"), ("AO", "ao")]:
            img_path = os.path.join(ctx.work_dir, f"{state.id}_{suffix}.png")
            result = _bake_pass(bake_type, obj, img_path, size)
            if result:
                bakes[suffix] = result

        state.artifacts["bakes"] = bakes
        state.artifacts["blender_object"] = obj.name
        return state
