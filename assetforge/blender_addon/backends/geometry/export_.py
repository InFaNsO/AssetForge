"""Stage 12 — Export. Writes the full asset (main mesh + LODs + collision) as GLB.

The file is named ``{asset_id}.glb`` inside the work directory. ``state.artifacts['exported']``
is set to the absolute path.

Note: filename is ``export_.py`` (underscore) to avoid shadowing the built-in ``export``
name that bpy itself uses.
"""
from __future__ import annotations

import os

import bpy

from assetforge.core.adapter import Backend, Capabilities, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import ensure_object, set_active


class ExportBackend(Backend):
    name = "gltf_export"
    stage = "export"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("export", input_types=("mesh",), output_types=("file",))

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        obj = ensure_object(state)
        if obj is None:
            raise RuntimeError("No mesh object to export")

        out_path = os.path.join(ctx.work_dir, f"{state.id}.glb")
        os.makedirs(ctx.work_dir, exist_ok=True)

        # Select: main mesh + LODs + collision object
        bpy.ops.object.select_all(action="DESELECT")
        _select_if_exists(obj.name)
        for lod_name in state.artifacts.get("lods", []):
            _select_if_exists(lod_name)
        col_name = state.artifacts.get("collision")
        if col_name:
            _select_if_exists(col_name)

        set_active(obj)

        bpy.ops.export_scene.gltf(
            filepath=out_path,
            export_format="GLB",
            use_selection=True,
            export_apply=True,          # apply modifiers
            export_normals=True,
            export_texcoords=True,
            export_materials="EXPORT",
        )

        state.artifacts["exported"] = out_path
        print(f"[AssetForge] exported -> {out_path}")
        return state


def _select_if_exists(name: str) -> None:
    obj = bpy.data.objects.get(name)
    if obj:
        obj.select_set(True)
