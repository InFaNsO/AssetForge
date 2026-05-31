"""N-panel for AssetForge (View3D > N-panel > AssetForge tab).

Each stage row has:
  [status icon]  [stage name]  [▶ run this stage]
"""
from __future__ import annotations

import bpy

from assetforge.core.asset_state import AssetState, StageStatus
from assetforge.core.stages import STAGES

_STATE_PROP = "assetforge_state_json"

_STATUS_ICON = {
    StageStatus.DONE:    "CHECKMARK",
    StageStatus.ACTIVE:  "PLAY",
    StageStatus.PENDING: "RADIOBUT_OFF",
    StageStatus.SKIPPED: "X",
    StageStatus.NA:      "BLANK1",
    StageStatus.FAILED:  "ERROR",
    StageStatus.MANUAL:  "HAND",
}

_STATUS_LABEL = {
    StageStatus.DONE:    "done",
    StageStatus.ACTIVE:  "running",
    StageStatus.PENDING: "",
    StageStatus.SKIPPED: "skipped",
    StageStatus.NA:      "n/a",
    StageStatus.FAILED:  "failed",
    StageStatus.MANUAL:  "manual",
}


class ASSETFORGE_PT_main(bpy.types.Panel):
    bl_label       = "AssetForge"
    bl_idname      = "ASSETFORGE_PT_main"
    bl_space_type  = "VIEW_3D"
    bl_region_type = "UI"
    bl_category    = "AssetForge"

    def draw(self, context):
        layout = self.layout
        sc = context.scene

        # ── Asset Settings ───────────────────────────────────────────────
        box = layout.box()
        box.label(text="Asset Settings", icon="SETTINGS")
        box.prop(sc, "assetforge_asset_type")
        box.prop(sc, "assetforge_mode")

        # ── Source ───────────────────────────────────────────────────────
        box = layout.box()
        box.label(text="Source", icon="IMAGE_DATA")
        box.prop(sc, "assetforge_source_type", expand=True)

        if sc.assetforge_source_type == "image":
            box.prop(sc, "assetforge_source_image", text="Image")
            if not sc.assetforge_source_image:
                box.label(text="Single subject, clean background", icon="INFO")
        else:
            box.prop(sc, "assetforge_source_prompt", text="Prompt")
            box.label(text="Text-to-3D: Tripo / Meshy / Hunyuan only", icon="INFO")

        # ── Generation Backend ───────────────────────────────────────────
        box = layout.box()
        box.label(text="Generation", icon="SHADERFX")
        box.prop(sc, "assetforge_gen_backend", text="Backend")

        if sc.assetforge_gen_backend in ("auto", "copilot3d"):
            row = box.row(align=True)
            row.prop(sc, "assetforge_copilot_glb", text="GLB")
            row.operator("assetforge.open_copilot", text="", icon="URL")
            if sc.assetforge_gen_backend == "copilot3d" and not sc.assetforge_copilot_glb:
                box.label(text="↑ Download GLB from Copilot 3D first", icon="ERROR")

        if sc.assetforge_gen_backend in ("tripo", "meshy", "hunyuan3d", "auto"):
            box.label(text="API keys → Edit › Prefs › Add-ons › AssetForge",
                      icon="KEYINGSET")

        # ── Stage Rail ───────────────────────────────────────────────────
        raw   = sc.get(_STATE_PROP)
        state = AssetState.from_json(raw) if raw else None

        box = layout.box()
        header = box.row()
        header.label(text="Pipeline Stages", icon="NODETREE")
        header.label(text="▶ = run stage alone")

        for s in STAGES:
            status = state.status(s.key) if state else StageStatus.PENDING
            is_na  = (status == StageStatus.NA)

            row = box.row(align=True)
            row.enabled = not is_na

            # Status icon
            row.label(text="", icon=_STATUS_ICON.get(status, "DOT"))

            # Stage name + optional status tag
            tag   = _STATUS_LABEL.get(status, "")
            label = f"{s.number:>2}. {s.name}" + (f"  [{tag}]" if tag else "")
            row.label(text=label)

            # Per-stage run button — right-aligned, small
            op = row.operator(
                "assetforge.run_stage",
                text="",
                icon="PLAY",
                emboss=True,
            )
            op.stage_key = s.key

        # ── Actions ──────────────────────────────────────────────────────
        layout.separator()
        row = layout.row(align=True)
        row.scale_y = 1.3
        row.operator("assetforge.run_to_end", icon="PLAY")
        row.operator("assetforge.reset_state", text="", icon="TRASH")


def register() -> None:
    bpy.utils.register_class(ASSETFORGE_PT_main)


def unregister() -> None:
    bpy.utils.unregister_class(ASSETFORGE_PT_main)
