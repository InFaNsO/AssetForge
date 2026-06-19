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
import threading
import traceback
import uuid as _uuid
from typing import Optional

import bpy

from assetforge.core.adapter import RunContext, RunMode
from assetforge.core.asset_state import AssetState, SourceKind, StageStatus
from assetforge.core.provenance import ProvenanceEntry
from assetforge.core.resolver import resolve
from assetforge.core.secrets import DictSecretStore, get_api_key
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


def apply_kimodo_animation(armature_name: Optional[str] = None,
                           action_name: str = "KimodoMotion") -> dict:
    """Convert the stored Kimodo NPZ into Blender FCurves on the scene armature.

    Must be called on the main thread AFTER poll() returns "done" for a Kimodo
    animate job. The NPZ path is read from state.artifacts["animations"]["kimodo"].

    ``armature_name``: optional explicit armature object name in the scene.
    If omitted, the armature with the most children is used (the char rig).
    """
    try:
        state = _load_state()
        if state is None:
            return _err("no state — call setup() first")

        npz_path = (state.artifacts.get("animations") or {}).get("kimodo")
        if not npz_path or not os.path.exists(str(npz_path)):
            return _err(
                "no Kimodo NPZ in state; run start('animate', "
                "params={'motion_prompt': '...'}) then poll(), then call this"
            )

        arm_obj = (bpy.data.objects.get(armature_name) if armature_name
                   else _find_armature_in_scene())
        if arm_obj is None:
            return _err("no armature found in scene — import the rigged GLB first")

        from assetforge.core.backends.kimodo.kimodo import npz_to_blender_action
        action = npz_to_blender_action(str(npz_path), arm_obj, action_name)

        # Push into NLA track so the action survives subsequent animation imports
        # and is picked up by the Unity FBX exporter as a separate clip.
        if arm_obj.animation_data is None:
            arm_obj.animation_data_create()
        cur = arm_obj.animation_data.action
        if cur and cur != action:
            cur.use_fake_user = True
        track = arm_obj.animation_data.nla_tracks.new()
        track.name = action_name
        strip = track.strips.new(action_name, 1, action)
        strip.name = action_name
        arm_obj.animation_data.action = None  # NLA in control

        state.metadata.setdefault("animate", {}).update({
            "kimodo_action": action.name,
            "kimodo_frames": int(action.frame_range[1]),
        })
        _save_state(state)
        frames = int(action.frame_range[1])
        return {"ok": True, "action": action.name,
                "armature": arm_obj.name,
                "frames": frames}
    except Exception as exc:
        return _err(f"{exc}", trace=traceback.format_exc())


def export_unity(embed_textures: bool = False,
                 armature_name: Optional[str] = None) -> dict:
    """Export the current asset as a Unity-ready FBX.

    Handles:
      - Meshy 0.01 armature scale: applied in-place before export so Unity
        reads bone positions in metres, not centimetres.
      - Unity coordinate system: Y-up, -Z forward.
      - All Blender actions baked into FBX animation clips.
      - Armature + all skinned meshes exported together.
      - ``embed_textures=True``: PBR maps embedded inside the FBX (larger file,
        self-contained). Default False: textures copied alongside FBX.

    Output path is stored in state.artifacts["exported_unity"].
    """
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")

    params: dict = {"embed_textures": embed_textures}
    if armature_name:
        params["armature_name"] = armature_name

    return run_stage("export", backend="unity_fbx_export", params=params)


def import_animation_glb(glb_path: str, action_name: str,
                          armature_name: Optional[str] = None) -> dict:
    """Import a Meshy/GLTF animation GLB and push its action onto the scene armature's NLA.

    Meshy animation GLBs share the same Mixamo bone layout as Meshy-rigged characters,
    so the action retargets directly without remapping. The imported GLB geometry is
    discarded; only the action is kept (use_fake_user=True) in an NLA track.

    ``glb_path``: absolute or Blender-relative path to the animation GLB.
    ``action_name``: what to name the resulting Blender action.
    ``armature_name``: explicit armature object name; auto-detected if omitted.
    """
    try:
        glb_path_abs = bpy.path.abspath(str(glb_path))
        if not os.path.exists(glb_path_abs):
            return _err(f"GLB not found: {glb_path_abs}")

        our_arm = (bpy.data.objects.get(armature_name) if armature_name
                   else _find_armature_in_scene())
        if our_arm is None:
            return _err("no armature in scene — import the rigged character first")

        before_objs = set(o.name for o in bpy.data.objects)
        before_actions = set(a.name for a in bpy.data.actions)

        bpy.ops.import_scene.gltf(filepath=glb_path_abs)

        new_objs = [o for o in bpy.data.objects if o.name not in before_objs]
        new_actions = [a for a in bpy.data.actions if a.name not in before_actions]

        if not new_actions:
            for o in new_objs:
                bpy.data.objects.remove(o, do_unlink=True)
            return _err(f"no animation action found in {glb_path}")

        # Take the action with the most frames (the real motion, not a bind pose)
        anim_action = max(new_actions, key=lambda a: a.frame_range[1])
        anim_action.name = action_name
        anim_action.use_fake_user = True

        # Preserve any current active action
        if our_arm.animation_data is None:
            our_arm.animation_data_create()
        cur = our_arm.animation_data.action
        if cur and cur != anim_action:
            cur.use_fake_user = True

        # Push into NLA track — each animation becomes a separate clip in Unity
        our_arm.animation_data.action = anim_action
        track = our_arm.animation_data.nla_tracks.new()
        track.name = action_name
        strip = track.strips.new(action_name, int(anim_action.frame_range[0]), anim_action)
        strip.name = action_name
        our_arm.animation_data.action = None  # NLA in control

        # Discard imported duplicate geometry
        for o in new_objs:
            bpy.data.objects.remove(o, do_unlink=True)
        for mesh in [m for m in bpy.data.meshes if m.users == 0]:
            bpy.data.meshes.remove(mesh)
        for arm_data in [a for a in bpy.data.armatures
                         if a.users == 0 and a != our_arm.data]:
            bpy.data.armatures.remove(arm_data)

        # Safety net: heal any spiky-boned armature already in the scene. glTF stores
        # no bone tails, so a rig imported outside the mesh path (manual drag-in, raw
        # bpy.ops.import_scene.gltf, etc.) can carry the giant-tail "explosion" that
        # other import paths repair. Importing animations onto a rig is the most common
        # operation, so this idempotent whole-scene pass guarantees the rig is healed by
        # the next animation import. No-op on healthy (and already-fixed) rigs.
        try:
            from .backends.geometry.utils import fix_armature_bone_display
            healed = fix_armature_bone_display()
            if healed:
                print(f"[AssetForge] healed {healed} spiky bone tails in scene")
        except Exception as exc:
            print(f"[AssetForge] bone-display fix skipped: {exc}")

        frames = int(anim_action.frame_range[1] - anim_action.frame_range[0])
        print(f"[AssetForge] Imported '{action_name}' ({frames} fr) onto '{our_arm.name}'")
        return {"ok": True, "action": action_name, "frames": frames,
                "armature": our_arm.name}
    except Exception as exc:
        return _err(f"{exc}", trace=traceback.format_exc())


# ---------------------------------------------------------------------------
# BVH -> FBX conversion (vendored from mcsantiago/bvh2fbx, see local/bvh2fbx/).
# bvh2fbx runs `blender -b --python convert_fbx.py`; we instead run the same
# import-BVH -> export-FBX recipe INSIDE the already-open Blender (no subprocess
# spin-up, reuses the warm session). Each conversion happens in a throwaway scene
# so the user's current scene is never disturbed.
# ---------------------------------------------------------------------------

def _bvh_to_fbx_one(bvh: str, fbx: str, global_scale: float, axis_forward: str,
                    axis_up: str, frame_start: int) -> dict:
    """Convert one BVH to FBX in an isolated temp scene. Returns a structured dict."""
    bvh = bpy.path.abspath(bvh)
    if not (os.path.exists(bvh) and bvh.lower().endswith(".bvh")):
        return {"ok": False, "bvh": bvh, "error": "not a .bvh on disk"}
    if not fbx:
        fbx = os.path.splitext(bvh)[0] + ".fbx"
    os.makedirs(os.path.dirname(fbx) or ".", exist_ok=True)

    win = bpy.context.window
    orig_scene = win.scene
    tmp = bpy.data.scenes.new("AF_BVH2FBX")
    win.scene = tmp
    new = []
    try:
        before = set(bpy.data.objects)
        # bvh2fbx import recipe (convert_fbx.py). global_scale default raised from the
        # original 0.0001 to 0.01 because SOMA/Kimodo BVH is authored in centimetres.
        bpy.ops.import_anim.bvh(
            filepath=bvh, filter_glob="*.bvh", global_scale=float(global_scale),
            frame_start=int(frame_start), target='ARMATURE', use_fps_scale=False,
            use_cyclic=False, rotate_mode='NATIVE',
            axis_forward=axis_forward, axis_up=axis_up,
            update_scene_fps=False, update_scene_duration=True)
        new = [o for o in bpy.data.objects if o not in before]
        arm = next((o for o in new if o.type == 'ARMATURE'), None)
        frames = 0
        if arm and arm.animation_data and arm.animation_data.action:
            frames = int(arm.animation_data.action.frame_range[1])

        bpy.ops.object.select_all(action='DESELECT')
        for o in new:
            o.select_set(True)
        if new:
            bpy.context.view_layer.objects.active = new[0]
        bpy.ops.export_scene.fbx(
            filepath=fbx, use_selection=True, apply_scale_options='FBX_SCALE_NONE',
            axis_forward=axis_forward, axis_up=axis_up, add_leaf_bones=False,
            bake_anim=True, bake_anim_use_all_actions=False, bake_anim_use_nla_strips=False)
        return {"ok": os.path.exists(fbx), "bvh": bvh, "fbx": fbx, "frames": frames}
    except Exception as exc:
        return {"ok": False, "bvh": bvh, "error": str(exc)}
    finally:
        try:
            win.scene = orig_scene
            for o in list(tmp.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            bpy.data.scenes.remove(tmp)
            for a in [a for a in bpy.data.actions if a.users == 0]:
                bpy.data.actions.remove(a)
        except Exception:
            pass


def bvh_to_fbx(bvh: str, fbx: str = "", global_scale: float = 0.01,
               axis_forward: str = "Z", axis_up: str = "Y",
               frame_start: int = 1) -> dict:
    """Convert ONE .bvh to .fbx in the open Blender (isolated temp scene; the current
    scene is untouched). ``fbx`` defaults to the BVH path with a .fbx extension.
    ``global_scale`` 0.01 suits SOMA/Kimodo BVH (cm); bvh2fbx's original default was 0.0001."""
    try:
        return _bvh_to_fbx_one(bvh, fbx, global_scale, axis_forward, axis_up, frame_start)
    except Exception as exc:
        return _err(f"{exc}", trace=traceback.format_exc())


def bvh_to_fbx_bulk(src_dir: str = "", out_dir: str = "", paths: Optional[list] = None,
                    global_scale: float = 0.01, axis_forward: str = "Z",
                    axis_up: str = "Y", frame_start: int = 1) -> dict:
    """Bulk-convert BVH->FBX. Provide ``paths`` (list of .bvh files) OR ``src_dir``
    (globs every *.bvh in it). ``out_dir`` defaults to each file's own folder. Same
    isolated-temp-scene conversion as ``bvh_to_fbx``. Returns per-file results."""
    import glob as _glob
    try:
        if paths:
            files = [bpy.path.abspath(p) for p in paths]
        elif src_dir:
            files = sorted(_glob.glob(os.path.join(bpy.path.abspath(src_dir), "*.bvh")))
        else:
            return _err("provide paths=[...] or src_dir=...")
        if not files:
            return _err("no .bvh files found")

        od = ""
        if out_dir:
            od = bpy.path.abspath(out_dir)
            os.makedirs(od, exist_ok=True)

        results = []
        for f in files:
            out = os.path.join(od, os.path.splitext(os.path.basename(f))[0] + ".fbx") if od else ""
            results.append(_bvh_to_fbx_one(f, out, global_scale, axis_forward, axis_up, frame_start))
        converted = sum(1 for r in results if r.get("ok"))
        return {"ok": converted > 0, "total": len(files), "converted": converted,
                "results": results}
    except Exception as exc:
        return _err(f"{exc}", trace=traceback.format_exc())


def bvh_to_fbx_combined(fbx: str, src_dir: str = "", paths: Optional[list] = None,
                        global_scale: float = 0.01) -> dict:
    """Combine many BVH clips into ONE multi-clip FBX (one shared skeleton, one named take
    per clip -> Unity reads them as separate AnimationClips). Provide ``paths`` (a list of
    .bvh) OR ``src_dir`` (every *.bvh in it). Runs a FRESH headless Blender (vendored
    local/bvh2fbx/combine_fbx.py) so the take set is clean and the open scene is untouched."""
    import glob as _glob
    import subprocess
    try:
        if paths:
            files = [bpy.path.abspath(p) for p in paths]
        elif src_dir:
            files = sorted(_glob.glob(os.path.join(bpy.path.abspath(src_dir), "*.bvh")))
        else:
            return _err("provide paths=[...] or src_dir=...")
        files = [f for f in files if os.path.exists(f) and f.lower().endswith(".bvh")]
        if not files:
            return _err("no .bvh files found")
        fbx = bpy.path.abspath(fbx)
        os.makedirs(os.path.dirname(fbx) or ".", exist_ok=True)
        script = os.path.normpath(os.path.join(
            os.path.dirname(__file__), "..", "local", "bvh2fbx", "combine_fbx.py"))
        if not os.path.exists(script):
            return _err(f"combine script not found: {script}")
        cmd = [bpy.app.binary_path, "-b", "--python", script, "--",
               fbx, str(global_scale)] + files
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        ok = os.path.exists(fbx)
        out = {"ok": ok, "fbx": fbx, "count": len(files),
               "clips": [os.path.splitext(os.path.basename(f))[0] for f in files]}
        if not ok:
            out["error"] = "headless conversion produced no file"
            out["stdout"] = proc.stdout[-800:]
            out["stderr"] = proc.stderr[-800:]
        return out
    except Exception as exc:
        return _err(f"{exc}", trace=traceback.format_exc())


def _find_armature_in_scene():
    """Return the armature with the most children (the character rig), or None."""
    candidates = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    return max(candidates, key=lambda o: len(o.children), default=None)


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
    new_objs = [o for o in bpy.data.objects if o.name not in before]
    try:
        from .backends.geometry.utils import fix_armature_bone_display
        fix_armature_bone_display(new_objs)   # repair glTF spiky-bone tails
    except Exception as exc:
        print(f"[AssetForge] bone-display fix skipped: {exc}")
    new = [o.name for o in new_objs]
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


# ---------------------------------------------------------------------------
# Async layer — long Meshy/Kimodo calls run in a background thread so they don't
# freeze Blender's main thread (a multi-minute synchronous poll would). The thread
# only touches assetforge.core (urllib + files — bpy-free, thread-safe); the key is
# read on the main thread and passed in via a DictSecretStore. Mirrors how blender-mcp
# drives its own long generation jobs (create, then poll).
# ---------------------------------------------------------------------------

_JOBS: dict = {}


def _kimodo_url() -> Optional[str]:
    return os.environ.get("ASSETFORGE_KIMODO_URL")


def _core_backend(stage_key: str, name: Optional[str] = None):
    """Return (backend_instance, run_mode) for a stage — core-only, no bpy/registry."""
    from assetforge.core.backends.generation.meshy import MeshyBackend
    from assetforge.core.backends.kimodo.kimodo import KimodoBackend
    from assetforge.core.backends.meshy.animation import MeshyAnimationBackend
    from assetforge.core.backends.meshy.retexture import MeshyRetextureBackend
    from assetforge.core.backends.meshy.rigging import MeshyRiggingBackend
    from assetforge.core.backends.remesh.meshy_remesh import MeshyRemeshBackend

    if stage_key == "animate" and name == "kimodo":
        return KimodoBackend(api_url=_kimodo_url()), RunMode.LOCAL
    table = {
        "generate": MeshyBackend,
        "texture":  MeshyRetextureBackend,
        "rig":      MeshyRiggingBackend,
        "retopo":   MeshyRemeshBackend,
        "animate":  MeshyAnimationBackend,
    }
    cls = table.get(stage_key)
    return (cls(), RunMode.API) if cls else (None, None)


def _bg(jid, backend, mode, state, params, ctx):
    try:
        result = backend.run(mode, state, params, ctx)
        _JOBS[jid].update({"status": "done", "state": result.to_json()})
    except Exception as exc:
        _JOBS[jid].update({"status": "error", "error": str(exc),
                           "trace": traceback.format_exc()[-1400:]})


def start(stage_key: str, backend: Optional[str] = None,
          params: Optional[dict] = None) -> dict:
    """Kick off a long API/Kimodo stage in a background thread. Returns {job: id};
    poll it with poll(job). Use this (not run_stage) for generate/texture/rig/animate/
    retopo so Blender's main thread never blocks on the multi-minute call."""
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")
    be, mode = _core_backend(stage_key, backend)
    if be is None:
        return _err(f"no core backend for stage {stage_key!r}")
    key = get_api_key(get_secret_store(bpy.context), "meshy")
    if mode == RunMode.API and not key:
        return _err("no Meshy API key in AssetForge prefs")
    ctx = RunContext(secrets=DictSecretStore({"meshy": key or ""}),
                     work_dir=bpy.app.tempdir,
                     user_data=({"kimodo_url": _kimodo_url()} if _kimodo_url() else {}))
    jid = _uuid.uuid4().hex[:8]
    _JOBS[jid] = {"status": "running", "stage": stage_key, "backend": be.name}
    threading.Thread(target=_bg, args=(jid, be, mode, state, params or {}, ctx),
                     daemon=True).start()
    return {"ok": True, "job": jid, "stage": stage_key, "backend": be.name, "status": "running"}


def poll(jid: str, also_done: Optional[list] = None) -> dict:
    """Poll a background job. On completion, persist the resulting state to the scene
    and mark the stage (plus any ``also_done`` stages, e.g. uv/texture bundled with a
    combined generation) DONE."""
    job = _JOBS.get(jid)
    if not job:
        return _err(f"unknown job {jid!r}")
    if job["status"] == "running":
        return {"ok": True, "job": jid, "status": "running"}
    if job["status"] == "error":
        return _err(job.get("error", "job failed"), job=jid, trace=job.get("trace"))

    state = AssetState.from_json(job["state"])
    stage_key = job.get("stage")
    for sk in [stage_key] + list(also_done or []):
        if sk:
            state.set_status(sk, StageStatus.DONE)
            state.record(ProvenanceEntry.create(sk, job.get("backend", ""), "api", {}))
    _save_state(state)
    return {"ok": True, "job": jid, "status": "done", "backend": job.get("backend"),
            **_state_summary(state)}


# ---------------------------------------------------------------------------
# Bulk animation generation (Kimodo) — generate many clips on ONE warm container.
# Modal's max_containers=1 serializes them anyway; doing them as one batch keeps
# the container warm so each clip pays only its inference, not a fresh 10-min
# keep-warm (≈ $0.05/clip batched vs ≈ $0.22 standalone).
# ---------------------------------------------------------------------------

def start_batch(clips: Optional[list] = None) -> dict:
    """Kick off BULK Kimodo generation in a background thread (Blender stays
    responsive). ``clips`` = list of dicts: {name, motion_prompt, num_frames?,
    playback?}. Each NPZ is generated sequentially and saved to its own path.
    Poll with poll_batch(job); when done, apply_batch(job) retargets them all
    onto the rig. Returns {job}."""
    state = _load_state()
    if state is None:
        return _err("no state — call setup() first")
    url = _kimodo_url()
    if not url:
        return _err("no Kimodo URL — set ASSETFORGE_KIMODO_URL")
    if not clips:
        return _err("clips list is empty")
    jid = _uuid.uuid4().hex[:8]
    _JOBS[jid] = {"status": "running", "kind": "anim_batch",
                  "total": len(clips), "done": 0, "results": []}
    threading.Thread(target=_bg_batch,
                     args=(jid, list(clips), url, bpy.app.tempdir, state.id),
                     daemon=True).start()
    return {"ok": True, "job": jid, "total": len(clips), "status": "running"}


def _bg_batch(jid, clips, url, work_dir, sid):
    """Background worker: generate each clip's NPZ sequentially (bpy-free)."""
    import time as _t
    from assetforge.core.backends.kimodo.kimodo import _call_kimodo
    results = []
    for clip in clips:
        name = clip.get("name", "clip")
        t0 = _t.monotonic()
        rec = {"name": name, "num_frames": int(clip.get("num_frames", 196)),
               "playback": clip.get("playback", "once")}
        try:
            npz = _call_kimodo(url, clip["motion_prompt"], int(clip.get("num_frames", 196)))
            path = os.path.join(work_dir, f"{sid}_kimodo_{name}.npz")
            with open(path, "wb") as fh:
                fh.write(npz)
            rec.update({"ok": True, "npz": path, "secs": round(_t.monotonic() - t0, 1)})
        except Exception as exc:
            rec.update({"ok": False, "error": str(exc)[:300],
                        "secs": round(_t.monotonic() - t0, 1)})
        results.append(rec)
        _JOBS[jid]["done"] = len(results)
        _JOBS[jid]["results"] = results
    _JOBS[jid]["status"] = "done"
    _JOBS[jid]["total_secs"] = round(sum(r.get("secs", 0) for r in results), 1)


def poll_batch(jid: str) -> dict:
    """Poll a bulk animation job — progress (done/total) + per-clip status/timing."""
    job = _JOBS.get(jid)
    if not job:
        return _err(f"unknown job {jid!r}")
    return {"ok": True, "job": jid, "status": job.get("status"),
            "done": job.get("done", 0), "total": job.get("total", 0),
            "total_secs": job.get("total_secs"),
            "results": job.get("results", [])}


def apply_batch(jid: str, armature_name: Optional[str] = None) -> dict:
    """Retarget EVERY generated NPZ in the batch onto the scene armature as a
    named, fake-user action (the fixed Rx-90 retarget). Stores ``af_playback``
    (loop/once/hold) on each action. Run AFTER poll_batch reports 'done'.
    Main thread only (touches bpy)."""
    job = _JOBS.get(jid)
    if not job:
        return _err(f"unknown job {jid!r}")
    if job.get("status") != "done":
        return _err(f"job {jid} not finished ({job.get('status')})")
    arm = (bpy.data.objects.get(armature_name) if armature_name
           else _find_armature_in_scene())
    if arm is None:
        return _err("no armature in scene")
    from assetforge.core.backends.kimodo.kimodo import npz_to_blender_action
    applied = []
    for r in job.get("results", []):
        if not r.get("ok"):
            applied.append({"name": r["name"], "ok": False, "error": r.get("error")})
            continue
        try:
            action = npz_to_blender_action(r["npz"], arm, action_name=r["name"])
            action.use_fake_user = True
            action["af_playback"] = r.get("playback", "once")   # loop / once / hold metadata
            applied.append({"name": r["name"], "ok": True,
                            "frames": int(action.frame_range[1])})
        except Exception as exc:
            applied.append({"name": r["name"], "ok": False, "error": str(exc)[:300]})
    return {"ok": True, "job": jid, "applied": applied}
