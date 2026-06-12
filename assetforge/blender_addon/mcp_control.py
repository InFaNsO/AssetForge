"""Phase 7 — MCP control surface.

A thin, **structured-return** wrapper over the AssetForge pipeline so Claude can drive
the whole chain through blender-mcp's ``execute_blender_code``. Unlike the bpy operators
(which report to the Blender UI and return ``{'FINISHED'}``), every function here returns
a JSON-serializable ``dict`` and never raises for expected failures — errors come back as
``{"ok": False, "error": "..."}`` so the result prints cleanly over the MCP bridge::

    from assetforge.blender_addon import mcp_control as af
    print(af.setup(image=r"C:/path/turnaround.png", asset_type="humanoid"))
    print(af.generate(mode="combined"))   # Meshy image-to-3D (mesh + UV + PBR in one call)
    print(af.run_stage("rig"))            # Meshy rigging (Mixamo-compatible)
    print(af.import_mesh())

Locked asset flow (project memory): generate -> (UV+texture bundled by Meshy) -> LODs
(Meshy remesh) -> rig (Meshy) -> animate (Meshy library, else Kimodo on Modal) -> export.
State is stored on the scene (same key the panel uses) so the panel and MCP stay in sync.
"""
from __future__ import annotations

import os
import traceback
from typing import Optional

import bpy

from assetforge.core.adapter import RunContext, RunMode
from assetforge.core.asset_state import AssetState, SourceKind, StageStatus
from assetforge.core.provenance import ProvenanceEntry
from assetforge.core.resolver import resolve
from assetforge.core.stages import AssetType

from .backends.registry import build_blender_registry
from .prefs import get_secret_store

_STATE_PROP = "assetforge_state_json"

# Which Meshy/Kimodo backend each stage should prefer when driven from MCP.
# (The resolver alone would prefer local/algo by cost; the locked flow wants Meshy,
#  so we pick explicitly via user_choice unless the caller overrides.)
_PREFERRED = {
    "generate": "meshy",
    "retopo":   "meshy_remesh",
    "texture":  "meshy_retexture",
    "rig":      "meshy_rigging",
    "animate":  "meshy_animation",
}


# ---------------------------------------------------------------------------
# State + context plumbing (mirrors blender_addon/operators.py, but bpy-context-free
# where possible so it works the same when called head-driven over MCP).
# ---------------------------------------------------------------------------

def _scene():
    return bpy.context.scene


def _load_state() -> Optional[AssetState]:
    raw = _scene().get(_STATE_PROP)
    return AssetState.from_json(raw) if raw else None


def _save_state(state: AssetState) -> None:
    _scene()[_STATE_PROP] = state.to_json()


def _ctx() -> RunContext:
    user_data = {}
    kimodo_url = os.environ.get("ASSETFORGE_KIMODO_URL")  # Modal endpoint, when set
    if kimodo_url:
        user_data["kimodo_url"] = kimodo_url
    return RunContext(
        secrets=get_secret_store(bpy.context),
        work_dir=bpy.app.tempdir,
        user_data=user_data,
    )


def _registry():
    return build_blender_registry()


def _state_summary(state: AssetState) -> dict:
    return {
        "id": state.id,
        "asset_type": state.asset_type.value,
        "source": state.source_ref,
        "stage_status": {k: v.value for k, v in state.stage_status.items()},
        "artifacts": {k: v for k, v in state.artifacts.items()},
    }


def _err(msg: str, **extra) -> dict:
    d = {"ok": False, "error": msg}
    d.update(extra)
    return d


# ---------------------------------------------------------------------------
# Public MCP verbs
# ---------------------------------------------------------------------------

def setup(image: Optional[str] = None, prompt: Optional[str] = None,
          asset_type: str = "humanoid", reset: bool = True) -> dict:
    """Create (or reset) the pipeline state from an input image or text prompt."""
    try:
        if reset and _STATE_PROP in _scene():
            del _scene()[_STATE_PROP]

        if image:
            image = bpy.path.abspath(image)
            if not os.path.exists(image):
                return _err(f"image not found: {image}")
            kind, ref = SourceKind.IMAGE, image
            asset_id = os.path.splitext(os.path.basename(image))[0] or "asset"
        elif prompt:
            kind, ref = SourceKind.TEXT, prompt
            asset_id = ("asset_" + prompt[:24].replace(" ", "_")).rstrip("_")
        else:
            return _err("provide either image= or prompt=")

        try:
            at = AssetType(asset_type)
        except ValueError:
            return _err(f"unknown asset_type {asset_type!r}; "
                        f"use one of {[t.value for t in AssetType]}")

        state = AssetState(id=asset_id, source_kind=kind, source_ref=ref, asset_type=at)
        _save_state(state)
        return {"ok": True, **_state_summary(state)}
    except Exception as exc:  # pragma: no cover - bpy runtime
        return _err(f"{exc}", trace=traceback.format_exc())


def run_stage(stage_key: str, backend: Optional[str] = None,
              params: Optional[dict] = None) -> dict:
    """Resolve + run one stage. ``backend`` forces a specific backend by name;
    otherwise the locked-flow preference (Meshy) is used, falling back to the resolver."""
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")

    ctx = _ctx()
    chosen = backend or _PREFERRED.get(stage_key)
    if chosen:
        ctx.user_choice[stage_key] = chosen

    reg = _registry()
    res = resolve(stage_key, reg, ctx, state)
    if not res.ok:
        return _err(f"no backend for {stage_key!r}: {res.reason}",
                    requested=chosen)

    state.set_status(stage_key, StageStatus.ACTIVE)
    try:
        state = res.backend.run(res.mode, state, params or {}, ctx)
    except Exception as exc:
        state.set_status(stage_key, StageStatus.FAILED)
        _save_state(state)
        return _err(f"{stage_key} failed via {res.backend.name}: {exc}",
                    backend=res.backend.name, trace=traceback.format_exc())

    state.record(ProvenanceEntry.create(stage_key, res.backend.name,
                                         res.mode.value, params or {}))
    state.set_status(stage_key, StageStatus.DONE)
    _save_state(state)
    return {"ok": True, "stage": stage_key, "backend": res.backend.name,
            "mode": res.mode.value, "reason": res.reason, **_state_summary(state)}


def generate(mode: str = "combined", model: str = "meshy-6",
             style_prompt: Optional[str] = None,
             params: Optional[dict] = None) -> dict:
    """Stage 3 (+ bundled UV/texture).

    ``model``: "meshy-6" (default, hero/characters) or "meshy-5" (cheaper, background assets).
    ``mode``:
      * "combined" — one Meshy call returns mesh + UV + PBR; mark ``uv`` and ``texture`` DONE.
      * "separate" — Meshy generates geometry+UV only (should_texture=False), then Meshy
        Retexture runs as its own step (``style_prompt`` controls the restyle).
    """
    params = dict(params or {})
    meshy = dict(params.get("meshy", {}))
    meshy.setdefault("ai_model", model)
    meshy["should_texture"] = (mode == "combined")
    meshy["enable_pbr"] = (mode == "combined")
    params["meshy"] = meshy

    gen = run_stage("generate", params=params)
    if not gen.get("ok"):
        return gen

    state = _load_state()
    # UVs always come bundled with Meshy generation — never re-unwrap (would break the map).
    state.set_status("uv", StageStatus.DONE)
    state.record(ProvenanceEntry.create("uv", "meshy", "api", {"note": "bundled with generation"}))

    if mode == "combined":
        state.set_status("texture", StageStatus.DONE)
        state.record(ProvenanceEntry.create("texture", "meshy", "api",
                                             {"note": "PBR bundled with generation"}))
        _save_state(state)
        return {"ok": True, "mode": "combined", **_state_summary(state)}

    if mode == "separate":
        _save_state(state)
        tex_params = dict(params.get("texture", {}) if params else {})
        if style_prompt:
            tex_params["style_prompt"] = style_prompt
        return run_stage("texture", params=tex_params)

    return _err(f"unknown mode {mode!r}; use 'combined' or 'separate'")


def generate_lods(levels: Optional[list] = None) -> dict:
    """Stage 10 via Meshy Remesh: re-mesh the generated model at descending polycounts.

    ``levels`` is a list of target polycounts, e.g. [20000, 8000, 3000] -> LOD0..LOD2.
    The base (full-detail) mesh is preserved as the primary ``mesh`` artifact.
    """
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")
    levels = levels or [20000, 8000, 3000]

    base_mesh = state.artifacts.get("mesh")
    ctx = _ctx()
    ctx.user_choice["retopo"] = "meshy_remesh"
    reg = _registry()

    lods, errors = {}, []
    for i, poly in enumerate(levels):
        # Remesh prefers the Meshy generation task_id as input, so each call is
        # independent of the previous output; reset the local mesh ref just in case.
        state.artifacts["mesh"] = base_mesh
        res = resolve("retopo", reg, ctx, state)
        if not res.ok:
            return _err(f"no remesh backend: {res.reason}")
        try:
            state = res.backend.run(res.mode, state, {"target_polycount": int(poly)}, ctx)
            lods[f"LOD{i}"] = state.artifacts.get("mesh")
        except Exception as exc:
            errors.append(f"LOD{i}@{poly}: {exc}")

    state.artifacts["mesh"] = base_mesh        # keep full-detail as primary
    state.artifacts["lods"] = lods
    if lods:
        state.set_status("lod", StageStatus.DONE)
        state.record(ProvenanceEntry.create("lod", "meshy_remesh", "api",
                                             {"levels": levels}))
    _save_state(state)
    out = {"ok": bool(lods), "lods": lods, **_state_summary(state)}
    if errors:
        out["errors"] = errors
    return out


def animate(action_ids: Optional[list] = None, motion_prompt: Optional[str] = None) -> dict:
    """Stage 9. Library clip(s) via Meshy (``action_ids``), or generative motion via
    Kimodo-on-Modal (``motion_prompt``). The locked rule: use Meshy's library if the
    motion exists there, else Kimodo."""
    if motion_prompt:
        return run_stage("animate", backend="kimodo", params={"motion_prompt": motion_prompt})
    params = {"action_ids": action_ids} if action_ids else {}
    return run_stage("animate", backend="meshy_animation", params=params)


def import_mesh() -> dict:
    """Import the current mesh GLB into the Blender scene."""
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")
    mesh = state.artifacts.get("mesh")
    if not isinstance(mesh, str):
        return _err("no mesh artifact to import")
    path = bpy.path.abspath(mesh)
    if not (os.path.exists(path) and path.lower().endswith((".glb", ".gltf"))):
        return _err(f"mesh not a GLB/GLTF on disk: {path}")
    before = set(o.name for o in bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=path)
    except Exception as exc:
        return _err(f"import failed: {exc}", trace=traceback.format_exc())
    new = [o.name for o in bpy.data.objects if o.name not in before]
    state.artifacts["blender_object"] = new[0] if new else None
    _save_state(state)
    return {"ok": True, "imported": new, "path": path}


def status() -> dict:
    """Return the current pipeline state, or a note that none exists yet."""
    state = _load_state()
    if state is None:
        return {"ok": True, "state": None, "note": "no state — call setup() first"}
    return {"ok": True, **_state_summary(state),
            "provenance": [p.to_dict() for p in state.provenance]}


def reset() -> dict:
    """Clear the stored pipeline state for this scene."""
    if _STATE_PROP in _scene():
        del _scene()[_STATE_PROP]
    return {"ok": True}
