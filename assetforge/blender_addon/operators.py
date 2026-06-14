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
from assetforge.core.asset_state import AssetState, SourceKind, StageStatus
from assetforge.core.pipeline import Mode, Pipeline, ValidationResult, always_ok
from assetforge.core.provenance import ProvenanceEntry
from assetforge.core.resolver import resolve
from assetforge.core.stages import AssetType, stage as get_stage

from .backends.registry import build_blender_registry
from .prefs import get_secret_store

_STATE_PROP = "assetforge_state_json"
_COPILOT_URL = "https://copilot.microsoft.com/labs/experiments/copilot-3d"


def _registry():
    return build_blender_registry()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _build_fresh_state(context) -> AssetState:
    """Build a new AssetState from the current panel inputs."""
    source_type   = context.scene.assetforge_source_type
    source_image  = bpy.path.abspath(context.scene.assetforge_source_image or "").strip()
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


def _build_ctx_and_params(context) -> tuple:
    """Shared RunContext + params setup used by both operators."""
    ctx    = RunContext(secrets=get_secret_store(context), work_dir=bpy.app.tempdir)
    params: dict = {}

    backend_choice = context.scene.assetforge_gen_backend
    copilot_glb    = bpy.path.abspath(context.scene.assetforge_copilot_glb or "").strip()

    if copilot_glb and os.path.exists(copilot_glb):
        ctx.user_choice["generate"] = "copilot3d"
        params["generate"] = {"downloaded_glb": copilot_glb}
    elif backend_choice != "auto":
        ctx.user_choice["generate"] = backend_choice

    # Retopo settings — passed to the retopo backend
    params["retopo"] = {
        "retopo_mode": context.scene.assetforge_retopo_mode,
        "platform":    context.scene.assetforge_platform,
    }

    return ctx, params


def _import_if_needed(state: AssetState) -> None:
    """Import the generated GLB into the scene if a geometry backend hasn't already."""
    if state.artifacts.get("blender_object"):
        return
    mesh = state.artifacts.get("mesh")
    if not isinstance(mesh, str):
        return
    path = bpy.path.abspath(mesh)
    if not (os.path.exists(path) and path.lower().endswith((".glb", ".gltf"))):
        return
    try:
        before = set(o.name for o in bpy.data.objects)
        bpy.ops.import_scene.gltf(filepath=path)
        from .backends.geometry.utils import fix_armature_bone_display
        fix_armature_bone_display([o for o in bpy.data.objects if o.name not in before])
    except Exception as exc:
        print(f"[AssetForge] could not import {path}: {exc}")


# ---------------------------------------------------------------------------
# Operator: Run to End
# ---------------------------------------------------------------------------

class ASSETFORGE_OT_run_to_end(bpy.types.Operator):
    """Run all applicable stages with the resolver's chosen backends."""

    bl_idname  = "assetforge.run_to_end"
    bl_label   = "Run to End"
    bl_options = {"REGISTER"}

    def execute(self, context):
        state      = _load_or_build_state(context)
        ctx, params = _build_ctx_and_params(context)
        mode       = Mode(context.scene.assetforge_mode)

        if (context.scene.assetforge_gen_backend == "copilot3d"
                and not params.get("generate", {}).get("downloaded_glb")):
            self.report({"ERROR"},
                "Copilot 3D selected but no GLB provided. "
                "Download a GLB from Copilot 3D and set the GLB path field.")
            return {"CANCELLED"}

        if not state.source_ref:
            self.report({"ERROR"},
                "No source set. Provide an image, text prompt, or select a mesh.")
            return {"CANCELLED"}

        report = Pipeline(_registry(), mode=mode).run(state, ctx, params=params)
        _save_state(context, state)

        if report.ok:
            _import_if_needed(state)
            self.report({"INFO"}, "AssetForge: pipeline completed ✓")
        else:
            failed = [r.stage_key for r in report.results if r.status.value == "failed"]
            self.report({"WARNING"}, f"AssetForge: stopped at {', '.join(failed) or '?'}")
        print("[AssetForge] run report:\n" + report.summary())
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Operator: Run Single Stage
# ---------------------------------------------------------------------------

class ASSETFORGE_OT_run_stage(bpy.types.Operator):
    """Run one pipeline stage in isolation."""

    bl_idname  = "assetforge.run_stage"
    bl_label   = "Run Stage"
    bl_options = {"REGISTER"}

    stage_key: bpy.props.StringProperty()  # type: ignore

    def execute(self, context):
        try:
            s = get_stage(self.stage_key)
        except KeyError:
            self.report({"ERROR"}, f"Unknown stage: {self.stage_key!r}")
            return {"CANCELLED"}

        state       = _load_or_build_state(context)
        ctx, params = _build_ctx_and_params(context)

        # N/A check
        if state.status(self.stage_key) == StageStatus.NA:
            self.report({"WARNING"},
                f"Stage '{s.name}' is not applicable to "
                f"asset type '{state.asset_type.value}'")
            return {"CANCELLED"}

        # Resolve backend
        reg = _registry()
        res = resolve(self.stage_key, reg, ctx, state)
        if not res.ok:
            self.report({"ERROR"}, f"No backend for '{s.name}': {res.reason}")
            return {"CANCELLED"}

        # Run
        state.set_status(self.stage_key, StageStatus.ACTIVE)
        stage_params = params.get(self.stage_key, {})
        try:
            state = res.backend.run(res.mode, state, stage_params, ctx)
        except Exception as exc:
            state.set_status(self.stage_key, StageStatus.FAILED)
            _save_state(context, state)
            self.report({"ERROR"}, f"'{s.name}' failed: {exc}")
            print(f"[AssetForge] {self.stage_key} ERROR: {exc}")
            return {"FINISHED"}

        state.record(ProvenanceEntry.create(
            self.stage_key, res.backend.name, res.mode.value, stage_params))
        state.set_status(self.stage_key, StageStatus.DONE)
        _save_state(context, state)

        # Import mesh into scene after generate
        if self.stage_key == "generate":
            _import_if_needed(state)

        self.report({"INFO"},
            f"AssetForge: '{s.name}' done  [{res.backend.name} · {res.mode.value}]")
        print(f"[AssetForge] {self.stage_key} via {res.backend.name}:{res.mode.value} — {res.reason}")
        return {"FINISHED"}


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
# Operator: Open Copilot 3D
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
    ASSETFORGE_OT_run_stage,
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
        items=[
            ("image", "Image", "Generate from a reference image"),
            ("text",  "Text",  "Generate from a text prompt"),
        ],
        default="image",
    )
    bpy.types.Scene.assetforge_source_image = bpy.props.StringProperty(
        name="Reference image",
        description="Image for image-to-3D (PNG/JPG, single clear subject on clean background)",
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
        items=[
            ("auto",      "Auto",       "Resolver picks best available (recommended)"),
            ("copilot3d", "Copilot 3D", "Free — download GLB manually then set path below"),
            ("tripo",     "Tripo",      "Paid — needs Tripo API key in preferences"),
            ("meshy",     "Meshy",      "Paid — needs Meshy API key in preferences"),
            ("hunyuan3d", "Hunyuan3D",  "Paid — needs fal.ai key, highest quality"),
        ],
        default="auto",
    )
    bpy.types.Scene.assetforge_copilot_glb = bpy.props.StringProperty(
        name="Copilot 3D GLB",
        description="GLB downloaded from Copilot 3D (free path, no API key needed)",
        subtype="FILE_PATH",
        default="",
    )
    bpy.types.Scene.assetforge_retopo_mode = bpy.props.EnumProperty(
        name="Retopo mode",
        items=[
            ("gentle", "Gentle (auto)",
             "Automated Decimate — works well for props, may show artifacts on characters"),
            ("manual", "Manual",
             "Mark the stage as manual so you retopo by hand in Blender — best for characters"),
        ],
        default="gentle",
    )
    bpy.types.Scene.assetforge_platform = bpy.props.EnumProperty(
        name="Platform target",
        items=[
            ("mobile",  "Mobile",  "3 000–5 000 tris"),
            ("indie",   "Indie",   "8 000–15 000 tris"),
            ("console", "Console", "20 000–40 000 tris"),
        ],
        default="indie",
    )
    for c in _CLASSES:
        bpy.utils.register_class(c)


def unregister() -> None:
    for c in reversed(_CLASSES):
        bpy.utils.unregister_class(c)
    for prop in ("assetforge_asset_type", "assetforge_mode", "assetforge_source_type",
                 "assetforge_source_image", "assetforge_source_prompt",
                 "assetforge_gen_backend", "assetforge_copilot_glb",
                 "assetforge_retopo_mode", "assetforge_platform"):
        if hasattr(bpy.types.Scene, prop):
            delattr(bpy.types.Scene, prop)
