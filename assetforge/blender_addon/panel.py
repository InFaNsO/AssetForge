"""N-panel for AssetForge (View3D > N-panel > AssetForge tab).

Phase 3-5 UI: source picker, backend selector, stage status rail, action buttons.
The full guided/expert stage-rail UX with tooltips and cost preview is Phase 8.
"""
from __future__ import annotations

import bpy

from assetforge.core.asset_state import AssetState, StageStatus
from assetforge.core.stages import STAGES

_STATE_PROP = "assetforge_state_json"

_ICON = {
    StageStatus.DONE:    "CHECKMARK",
    StageStatus.ACTIVE:  "PLAY",
    StageStatus.PENDING: "RADIOBUT_OFF",
    StageStatus.SKIPPED: "X",
    StageStatus.NA:      "BLANK1",
    StageStatus.FAILED:  "ERROR",
    StageStatus.MANUAL:  "HAND",
}


class ASSETFORGE_PT_main(bpy.types.Panel):
    bl_label      = "AssetForge"
    bl_idname     = "ASSETFORGE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category   = "AssetForge"

    def draw(self, context):
        layout = self.layout
        sc = context.scene

        # ── Asset settings ──────────────────────────────────────────────
        box = layout.box()
        box.label(text="Asset Settings", icon="SETTINGS")
        box.prop(sc, "assetforge_asset_type")
        box.prop(sc, "assetforge_mode")

        # ── Source ──────────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Source", icon="IMAGE_DATA")
        box.prop(sc, "assetforge_source_type", expand=True)

        if sc.assetforge_source_type == "image":
            box.prop(sc, "assetforge_source_image", text="Image")
            if not sc.assetforge_source_image:
                box.label(text="Tip: single subject, clean background", icon="INFO")
        else:
            box.prop(sc, "assetforge_source_prompt", text="Prompt")
            box.label(text="Text-to-3D: Tripo / Meshy / Hunyuan only", icon="INFO")

        # ── Generation backend ──────────────────────────────────────────
        box = layout.box()
        box.label(text="Generation", icon="SHADERFX")
        box.prop(sc, "assetforge_gen_backend", text="Backend")

        # Always show Copilot 3D fields when that backend is selected,
        # or as the fallback hint when on Auto with no API keys.
        show_copilot = sc.assetforge_gen_backend in ("auto", "copilot3d")
        if show_copilot:
            row = box.row(align=True)
            row.prop(sc, "assetforge_copilot_glb", text="GLB")
            row.operator("assetforge.open_copilot", text="", icon="URL")
            if sc.assetforge_gen_backend == "copilot3d" and not sc.assetforge_copilot_glb:
                box.label(text="↑ Download GLB from Copilot 3D first", icon="ERROR")
            elif sc.assetforge_gen_backend == "auto" and not sc.assetforge_copilot_glb:
                box.label(text="Optional: free fallback via Copilot 3D", icon="INFO")

        if sc.assetforge_gen_backend in ("tripo", "meshy", "hunyuan3d", "auto"):
            box.label(text="API keys → Edit › Prefs › Add-ons › AssetForge",
                      icon="KEYINGSET")

        # ── Stage rail ──────────────────────────────────────────────────
        raw = sc.get(_STATE_PROP)
        state = AssetState.from_json(raw) if raw else None

        box = layout.box()
        box.label(text="Pipeline stages", icon="NODETREE")
        for s in STAGES:
            row = box.row(align=True)
            status = state.status(s.key) if state else StageStatus.PENDING
            icon   = _ICON.get(status, "DOT")
            row.label(text=f"{s.number:>2}. {s.name}", icon=icon)

        # ── Actions ─────────────────────────────────────────────────────
        layout.separator()
        layout.operator("assetforge.run_to_end", icon="PLAY")
        layout.operator("assetforge.reset_state", icon="TRASH")


def register() -> None:
    bpy.utils.register_class(ASSETFORGE_PT_main)


def unregister() -> None:
    bpy.utils.unregister_class(ASSETFORGE_PT_main)
