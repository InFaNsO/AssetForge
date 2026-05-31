"""Operators = the verbs (PROJECT_SPEC.md §4.1). The panel and (Phase 7) MCP both drive
these, so they never diverge.

Phase 0/1: operators drive the STUB registry so the chain runs without any real model.
Real backends are registered here as they land in Phases 1-6.
"""
from __future__ import annotations

import bpy

from assetforge.core.adapter import RunContext
from assetforge.core.asset_state import AssetState, SourceKind
from assetforge.core.backends.stubs import build_stub_registry
from assetforge.core.pipeline import Mode, Pipeline
from assetforge.core.stages import AssetType

from .prefs import get_secret_store

# Scene-stored asset state lives as JSON so it survives save/load (the contract is
# serializable by design — PROJECT_SPEC.md §4.3).
_STATE_PROP = "assetforge_state_json"


def _registry():
    # Single place to assemble the backend registry; swap stubs for real backends here.
    return build_stub_registry()


def _load_or_init_state(context) -> AssetState:
    raw = context.scene.get(_STATE_PROP)
    if raw:
        return AssetState.from_json(raw)
    obj = context.active_object
    return AssetState(
        id=(obj.name if obj else "asset"),
        source_kind=SourceKind.MESH,
        source_ref=(obj.name if obj else ""),
        asset_type=AssetType(context.scene.assetforge_asset_type),
    )


def _save_state(context, state: AssetState) -> None:
    context.scene[_STATE_PROP] = state.to_json()


class ASSETFORGE_OT_run_to_end(bpy.types.Operator):
    """Run all non-skipped stages with the resolver's chosen backends."""

    bl_idname = "assetforge.run_to_end"
    bl_label = "Run to End"
    bl_options = {"REGISTER"}

    def execute(self, context):
        state = _load_or_init_state(context)
        ctx = RunContext(secrets=get_secret_store(context), work_dir=bpy.app.tempdir)
        mode = Mode(context.scene.assetforge_mode)
        report = Pipeline(_registry(), mode=mode).run(state, ctx)
        _save_state(context, state)

        if report.ok:
            self.report({"INFO"}, "AssetForge: chain completed")
        else:
            failed = [r.stage_key for r in report.results if r.status.value == "failed"]
            self.report({"WARNING"}, f"AssetForge: stopped at {', '.join(failed) or '?'}")
        print("[AssetForge] run report:\n" + report.summary())
        return {"FINISHED"}


class ASSETFORGE_OT_reset_state(bpy.types.Operator):
    """Clear the stored pipeline state for this scene."""

    bl_idname = "assetforge.reset_state"
    bl_label = "Reset Pipeline State"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if _STATE_PROP in context.scene:
            del context.scene[_STATE_PROP]
        self.report({"INFO"}, "AssetForge: state reset")
        return {"FINISHED"}


_CLASSES = (ASSETFORGE_OT_run_to_end, ASSETFORGE_OT_reset_state)


def register() -> None:
    bpy.types.Scene.assetforge_asset_type = bpy.props.EnumProperty(
        name="Asset type",
        items=[(t.value, t.value.capitalize(), "") for t in AssetType],
        default=AssetType.STATIC.value,
    )
    bpy.types.Scene.assetforge_mode = bpy.props.EnumProperty(
        name="Mode",
        items=[("guided", "Guided", "Block on failed validation"),
               ("expert", "Expert", "Warn and continue")],
        default="guided",
    )
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister() -> None:
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
    del bpy.types.Scene.assetforge_asset_type
    del bpy.types.Scene.assetforge_mode
