"""Operators — the verbs. Both the panel and (Phase 7) MCP drive these.

Scene properties registered here:
  assetforge_asset_type    Static / Humanoid / etc.
  assetforge_mode          Guided / Expert
  assetforge_source_type   image | text
  assetforge_source_image  FILE_PATH — reference image for image-to-3D
  assetforge_source_prompt STRING    — text prompt for text-to-3D
  assetforge_gen_backend   auto | copilot3d | tripo | meshy | hunyuan3d
  assetforge_copilot_glb   FILE_PATH — pre-downloaded GLB (free / offline path)
"""
from __future__ import annotations

import os

import bpy

from assetforge.core.adapter import RunContext
from assetforge.core.asset_state import AssetState, SourceKind
from assetforge.core.pipeline import Mode, Pipeline
from assetforge.core.stages import AssetType

from .backends.registry import build_blender_registry
from .prefs import get_secret_store

_STATE_PROP = "assetforge_state_json"
_COPILOT_URL = "https://copilot.microsoft.com/labs/experiments/copilot-3d"


def _registry():
    return build_blender_registry()


# ---------------------------------------------------------------------------
# State init
# ---------------------------------------------------------------------------

def _build_fresh_state(context) -> AssetState:
    """Build an AssetState from the current panel inputs (no saved state)."""
    source_type  = context.scene.assetforge_source_type
    source_image = bpy.path.abspath(context.scene.assetforge_source_image or "").strip()
    source_prompt = (context.scene.assetforge_source_prompt or "").strip()

    if source_type == "image" and source_image and os.path.exists(source_image):
        source_kind = SourceKind.IMAGE
        source_ref  = source_image
        asset_id    = os.path.splitext(os.path.basename(source_image))[0] or "asset"
    elif source_type == "text" and source_prompt:
        source_kind = SourceKind.TEXT
        source_ref  = source_prompt
        asset_id    = ("asset_" + source_prompt[:20].replace(" ", "_")).rstrip("_")
    else:
        obj = context.active_object
        source_kind = SourceKind.MESH
        source_ref  = obj.name if obj else ""
        asset_id    = obj.name if obj else "asset"

    return AssetState(
        id=asset_id,
        source_kind=source_kind,
        source_ref=source_ref,
        asset_type=AssetType(context.scene.assetforge_asset_type),
    )


def _load_or_build_state(context) -> AssetState:
    raw = context.scene.get(_STATE_PROP)
    if raw:
        return AssetState.from_json(raw)
    return _build_fresh_state(context)


def _save_state(context, state: AssetState) -> None:
    context.scene[_STATE_PROP] = state.to_json()


# ---------------------------------------------------------------------------
# Operator: Run to End
# ---------------------------------------------------------------------------

class ASSETFORGE_OT_run_to_end(bpy.types.Operator):
    """Run all applicable stages with the resolver's chosen backends."""

    bl_idname = "assetforge.run_to_end"
    bl_label  = "Run to End"
    bl_options = {"REGISTER"}

    def execute(self, context):
        state = _load_or_build_state(context)
        ctx   = RunContext(secrets=get_secret_store(context), work_dir=bpy.app.tempdir)
        mode  = Mode(context.scene.assetforge_mode)
        params: dict = {}

        backend_choice = context.scene.assetforge_gen_backend
        copilot_glb    = bpy.path.abspath(context.scene.assetforge_copilot_glb or "").strip()

        # --- Wire up generation source ---
        if copilot_glb and os.path.exists(copilot_glb):
            # Pre-downloaded GLB: always use the Copilot 3D adapter's manual path.
            ctx.user_choice["generate"] = "copilot3d"
            params["generate"] = {"downloaded_glb": copilot_glb}

        elif backend_choice != "auto":
            # User explicitly chose a backend.
            ctx.user_choice["generate"] = backend_choice
            if backend_choice == "copilot3d":
                self.report({"ERROR"},
                    "Copilot 3D selected but no GLB provided. "
                    "Download a GLB from Copilot 3D and set the GLB path field.")
                return {"CANCELLED"}

        # For Tripo / Meshy / Hunyuan the source_ref (image path or prompt) is
        # already stored in state — the adapters read it from there.

        if not state.source_ref:
            self.report({"ERROR"},
                "No source provided. Set an image path, text prompt, or select "
                "a mesh object before running.")
            return {"CANCELLED"}

        report = Pipeline(_registry(), mode=mode).run(state, ctx, params=params)
        _save_state(context, state)

        if report.ok:
            self._import_if_needed(state)
            self.report({"INFO"}, "AssetForge: pipeline completed ✓")
        else:
            failed = [r.stage_key for r in report.results if r.status.value == "failed"]
            self.report({"WARNING"}, f"AssetForge: stopped at {', '.join(failed) or '?'}")
        print("[AssetForge] run report:\n" + report.summary())
        return {"FINISHED"}

    @staticmethod
    def _import_if_needed(state) -> None:
        if state.artifacts.get("blender_object"):
            return  # already in scene (geometry backends imported it)
        mesh = state.artifacts.get("mesh")
        if not isinstance(mesh, str):
            return
        path = bpy.path.abspath(mesh)
        if not (os.path.exists(path) and path.lower().endswith((".glb", ".gltf"))):
            return
        try:
            bpy.ops.import_scene.gltf(filepath=path)
        except Exception as exc:
            print(f"[AssetForge] could not import {path}: {exc}")


# ---------------------------------------------------------------------------
# Operator: Reset
# ---------------------------------------------------------------------------

class ASSETFORGE_OT_reset_state(bpy.types.Operator):
    """Clear the stored pipeline state for this scene."""

    bl_idname  = "assetforge.reset_state"
    bl_label   = "Reset Pipeline State"
    bl_options = {"REGISTER"}

    def execute(self, context):
        if _STATE_PROP in context.scene:
            del context.scene[_STATE_PROP]
        self.report({"INFO"}, "AssetForge: state reset")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operator: Open Copilot 3D in browser
# ---------------------------------------------------------------------------

class ASSETFORGE_OT_open_copilot(bpy.types.Operator):
    """Open Microsoft Copilot 3D in your browser (free generation)."""

    bl_idname  = "assetforge.open_copilot"
    bl_label   = "Open Copilot 3D"
    bl_options = {"REGISTER"}

    def execute(self, context):
        import webbrowser
        webbrowser.open(_COPILOT_URL)
        self.report({"INFO"}, "Opened Copilot 3D in browser")
        return {"FINISHED"}


_CLASSES = (
    ASSETFORGE_OT_run_to_end,
    ASSETFORGE_OT_reset_state,
    ASSETFORGE_OT_open_copilot,
)


# ---------------------------------------------------------------------------
# Register
# ---------------------------------------------------------------------------

def register() -> None:
    bpy.types.Scene.assetforge_asset_type = bpy.props.EnumProperty(
        name="Asset type",
        items=[(t.value, t.value.capitalize(), "") for t in AssetType],
        default=AssetType.STATIC.value,
    )
    bpy.types.Scene.assetforge_mode = bpy.props.EnumProperty(
        name="Mode",
        items=[("guided", "Guided", "Block on validation failure"),
               ("expert", "Expert", "Warn and continue")],
        default="guided",
    )
    bpy.types.Scene.assetforge_source_type = bpy.props.EnumProperty(
        name="Source",
        description="What to generate from",
        items=[
            ("image",  "Image",  "Generate from a reference image"),
            ("text",   "Text",   "Generate from a text prompt"),
        ],
        default="image",
    )
    bpy.types.Scene.assetforge_source_image = bpy.props.StringProperty(
        name="Reference image",
        description="Image to feed to the generation backend (PNG/JPG, single clear subject)",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.assetforge_source_prompt = bpy.props.StringProperty(
        name="Prompt",
        description="Text description of the asset (e.g. 'a wooden barrel')",
        default="",
    )
    bpy.types.Scene.assetforge_gen_backend = bpy.props.EnumProperty(
        name="Backend",
        description="Which generation backend to use",
        items=[
            ("auto",       "Auto",        "Resolver picks best available (recommended)"),
            ("copilot3d",  "Copilot 3D",  "Free — download GLB manually then set path below"),
            ("tripo",      "Tripo",       "Paid — needs Tripo API key in preferences"),
            ("meshy",      "Meshy",       "Paid — needs Meshy API key in preferences"),
            ("hunyuan3d",  "Hunyuan3D",   "Paid — needs fal.ai key, highest quality"),
        ],
        default="auto",
    )
    bpy.types.Scene.assetforge_copilot_glb = bpy.props.StringProperty(
        name="Copilot 3D GLB",
        description="GLB you downloaded from Copilot 3D (free path, no API key needed)",
        subtype="FILE_PATH",
        default="",
    )
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister() -> None:
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
    for prop in ("assetforge_asset_type", "assetforge_mode", "assetforge_source_type",
                 "assetforge_source_image", "assetforge_source_prompt",
                 "assetforge_gen_backend", "assetforge_copilot_glb"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
