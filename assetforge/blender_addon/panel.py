"""Minimal N-panel. The full guided stage-rail UX is Phase 8 (DEVELOPMENT_PLAN.md §4);
this just exposes the stage statuses and the run button so the operators are usable now.
"""
from __future__ import annotations

import bpy

from assetforge.core.asset_state import AssetState, StageStatus
from assetforge.core.stages import STAGES

_STATE_PROP = "assetforge_state_json"

_ICON = {
    StageStatus.DONE: "CHECKMARK",
    StageStatus.ACTIVE: "PLAY",
    StageStatus.PENDING: "RADIOBUT_OFF",
    StageStatus.SKIPPED: "X",
    StageStatus.NA: "BLANK1",
    StageStatus.FAILED: "ERROR",
    StageStatus.MANUAL: "HAND",
}


class ASSETFORGE_PT_main(bpy.types.Panel):
    bl_label = "AssetForge"
    bl_idname = "ASSETFORGE_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AssetForge"

    def draw(self, context):
        layout = self.layout
        layout.prop(context.scene, "assetforge_asset_type")
        layout.prop(context.scene, "assetforge_mode")
        layout.prop(context.scene, "assetforge_copilot_glb")

        raw = context.scene.get(_STATE_PROP)
        state = AssetState.from_json(raw) if raw else None

        box = layout.box()
        box.label(text="Stages")
        for s in STAGES:
            row = box.row()
            status = state.status(s.key) if state else StageStatus.PENDING
            row.label(text=f"{s.number}. {s.name}", icon=_ICON.get(status, "DOT"))

        layout.operator("assetforge.run_to_end", icon="PLAY")
        layout.operator("assetforge.reset_state", icon="TRASH")


def register() -> None:
    bpy.utils.register_class(ASSETFORGE_PT_main)


def unregister() -> None:
    bpy.utils.unregister_class(ASSETFORGE_PT_main)
