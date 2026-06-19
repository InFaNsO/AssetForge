# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2"]
# ///
"""AssetForge MCP server — exposes the AssetForge pipeline as first-class MCP tools.

This is a *thin* server. Every tool dispatches an ``execute_code`` command to the
blender-mcp addon's socket (localhost:9876) which calls the matching verb in
``assetforge.blender_addon.mcp_control`` and prints a sentinel-wrapped JSON result.
The server extracts that JSON and returns it. No AssetForge/bpy code runs in this
process — only the ``mcp`` SDK + stdlib — so it has no heavy dependencies and is
decoupled from blender-mcp's own release cycle.

Requirements at run time:
  * Blender is open with the **blender-mcp** addon "Connect"ed (its socket on 9876).
  * The **AssetForge** addon is installed/enabled (so ``assetforge.blender_addon
    .mcp_control`` is importable from Blender's Python).

Launched by Claude via .mcp.json with:  uv run --no-project <this file>
(uv reads the PEP 723 metadata above and provisions ``mcp`` automatically.)
"""
from __future__ import annotations

import base64
import json
import socket
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

HOST = "localhost"
PORT = 9876
_BEGIN = "<<AF_RESULT>>"
_END = "<<END_AF_RESULT>>"
_SOCK_TIMEOUT = 300.0  # generous; long stages use af_start/af_poll and return instantly

mcp = FastMCP("assetforge")

# 13 pipeline stage keys (assetforge/core/stages.py), for discoverability / validation.
STAGE_KEYS = ["concept", "blockout", "generate", "retopo", "uv", "bake",
              "texture", "rig", "animate", "lod", "collision", "export", "validate"]


# ---------------------------------------------------------------------------
# Blender socket transport (mirrors blender-mcp's BlenderConnection, minimal)
# ---------------------------------------------------------------------------

def _recv_full(sock: socket.socket, buffer_size: int = 8192) -> bytes:
    """Read until the accumulated bytes parse as one complete JSON object."""
    chunks = []
    sock.settimeout(_SOCK_TIMEOUT)
    while True:
        chunk = sock.recv(buffer_size)
        if not chunk:
            if not chunks:
                raise ConnectionError("Connection closed before any data was received")
            break
        chunks.append(chunk)
        try:
            data = b"".join(chunks)
            json.loads(data.decode("utf-8"))
            return data
        except json.JSONDecodeError:
            continue  # incomplete, keep reading
    return b"".join(chunks)


def _send_command(command_type: str, params: dict) -> dict:
    """Open a short-lived socket to the Blender addon, send one command, return its result."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(_SOCK_TIMEOUT)
    try:
        sock.connect((HOST, PORT))
        sock.sendall(json.dumps({"type": command_type, "params": params}).encode("utf-8"))
        resp = json.loads(_recv_full(sock).decode("utf-8"))
    finally:
        try:
            sock.close()
        except Exception:
            pass
    if resp.get("status") == "error":
        raise RuntimeError(resp.get("message", "Unknown error from Blender"))
    return resp.get("result", {})


def _call(verb: str, **kwargs: Any) -> dict:
    """Run an mcp_control verb inside Blender and return its structured dict result.

    kwargs are base64+JSON encoded so arbitrary prompt/path text can't break the
    Python snippet we send (no quote/newline escaping hazards).
    """
    payload = base64.b64encode(json.dumps(kwargs).encode("utf-8")).decode("ascii")
    code = (
        "import json, base64\n"
        "from assetforge.blender_addon import mcp_control as af\n"
        "_kw = json.loads(base64.b64decode('" + payload + "').decode('utf-8'))\n"
        "_res = af." + verb + "(**_kw)\n"
        "print('" + _BEGIN + "' + json.dumps(_res) + '" + _END + "')\n"
    )
    try:
        result = _send_command("execute_code", {"code": code})
    except (ConnectionRefusedError, ConnectionError, OSError) as exc:
        return {"ok": False, "error": (
            f"Cannot reach Blender on {HOST}:{PORT}. Open Blender, then in the "
            f"BlenderMCP sidebar click 'Connect to MCP server'. ({exc})")}
    except Exception as exc:
        return {"ok": False, "error": f"Blender command failed: {exc}"}

    stdout = result.get("result", "") if isinstance(result, dict) else str(result)
    if _BEGIN in stdout and _END in stdout:
        seg = stdout.split(_BEGIN, 1)[1].split(_END, 1)[0]
        try:
            return json.loads(seg)
        except json.JSONDecodeError as exc:
            return {"ok": False, "error": f"Malformed AssetForge result: {exc}",
                    "raw": seg[:1000]}
    return {"ok": False,
            "error": "No AssetForge result marker in Blender output "
                     "(is the AssetForge addon enabled?)",
            "raw_stdout": stdout[-2000:]}


def _out(d: dict) -> str:
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Tools — one per mcp_control verb (these docstrings are what the model sees)
# ---------------------------------------------------------------------------

@mcp.tool()
def af_stages() -> str:
    """List the 13 AssetForge pipeline stage keys, in order. No Blender call."""
    return _out({"ok": True, "stages": STAGE_KEYS})


@mcp.tool()
def af_status() -> str:
    """Get the current AssetForge pipeline state: asset id, source, per-stage status,
    artifacts (mesh/rig/animations/exports) and provenance. Returns state=None if no
    pipeline has been set up yet."""
    return _out(_call("status"))


@mcp.tool()
def af_setup(image: str = "", prompt: str = "", asset_type: str = "humanoid",
            reset: bool = True) -> str:
    """Create (or reset) a pipeline from a source image path OR a text prompt.
    Provide exactly one of `image` / `prompt`. `asset_type` is e.g. 'humanoid',
    'prop', 'environment'. With reset=True (default) any existing state is cleared first."""
    return _out(_call("setup", image=image, prompt=prompt,
                      asset_type=asset_type, reset=reset))


@mcp.tool()
def af_reset() -> str:
    """Clear the stored AssetForge pipeline state for the current scene."""
    return _out(_call("reset"))


@mcp.tool()
def af_run_stage(stage_key: str, backend: str = "", params: Optional[dict] = None) -> str:
    """Run ONE pipeline stage synchronously. `stage_key` is one of af_stages().
    `backend` optionally forces a specific backend (else the locked Meshy-flow
    preference is used). `params` are stage-specific options.
    NOTE: for long API stages (generate/texture/rig/animate/retopo) prefer
    af_start + af_poll so Blender's main thread never blocks."""
    return _out(_call("run_stage", stage_key=stage_key, backend=backend, params=params))


@mcp.tool()
def af_generate(mode: str = "combined", model: str = "meshy-6",
               style_prompt: str = "", params: Optional[dict] = None) -> str:
    """Stage 3 generation via Meshy (+ bundled UV/texture). `model`: 'meshy-6'
    (hero/characters) or 'meshy-5' (cheaper). `mode`: 'combined' (mesh+UV+PBR in one
    call, marks uv+texture done) or 'separate' (geometry+UV only, then a Retexture
    pass driven by `style_prompt`). Synchronous — for big jobs use af_start('generate')."""
    return _out(_call("generate", mode=mode, model=model,
                      style_prompt=style_prompt, params=params))


@mcp.tool()
def af_generate_lods(levels: Optional[list] = None) -> str:
    """Stage 10 — generate LODs via Meshy Remesh at descending polycounts, e.g.
    levels=[20000, 8000, 3000] -> LOD0..LOD2. The full-detail mesh is kept as primary.
    Defaults to [20000, 8000, 3000] when omitted."""
    return _out(_call("generate_lods", levels=levels))


@mcp.tool()
def af_animate(action_ids: Optional[list] = None, motion_prompt: str = "") -> str:
    """Stage 9 animation. Provide `action_ids` for Meshy library clip(s), OR a
    `motion_prompt` for generative motion via Kimodo-on-Modal. Locked rule: use the
    Meshy library if the motion exists there, else Kimodo. Synchronous — for Kimodo
    use af_start('animate', backend='kimodo', params={'motion_prompt': ...}) + af_poll."""
    return _out(_call("animate", action_ids=action_ids, motion_prompt=motion_prompt))


@mcp.tool()
def af_apply_kimodo_animation(armature_name: str = "",
                             action_name: str = "KimodoMotion") -> str:
    """Convert the stored Kimodo NPZ into a Blender action on the scene armature and
    push it onto an NLA track (so it survives later imports and exports as its own
    Unity clip). Call AFTER af_poll reports 'done' for a Kimodo animate job.
    `armature_name` is auto-detected (rig = armature with most children) if omitted."""
    return _out(_call("apply_kimodo_animation", armature_name=armature_name,
                      action_name=action_name))


@mcp.tool()
def af_import_mesh() -> str:
    """Import the current mesh GLB artifact into the Blender scene."""
    return _out(_call("import_mesh"))


@mcp.tool()
def af_import_animation_glb(glb_path: str, action_name: str,
                           armature_name: str = "") -> str:
    """Import a Meshy/GLTF animation GLB and push its action onto the scene armature's
    NLA as `action_name` (imported geometry is discarded). `armature_name` auto-detected
    if omitted."""
    return _out(_call("import_animation_glb", glb_path=glb_path,
                      action_name=action_name, armature_name=armature_name))


@mcp.tool()
def af_export_unity(embed_textures: bool = False, armature_name: str = "") -> str:
    """Stage 12 — export the current asset as a Unity-ready FBX (Y-up/-Z forward,
    0.01 armature scale applied, all actions baked as clips). embed_textures=True
    embeds PBR maps in the FBX; default copies them alongside. Path is returned in
    artifacts['exported_unity']."""
    return _out(_call("export_unity", embed_textures=embed_textures,
                      armature_name=armature_name))


@mcp.tool()
def af_start(stage_key: str, backend: str = "", params: Optional[dict] = None) -> str:
    """Start a LONG API/Kimodo stage (generate/texture/rig/animate/retopo) in a
    background thread so Blender never blocks. Returns {"job": "<id>"}; poll it with
    af_poll. For Kimodo motion: af_start('animate', backend='kimodo',
    params={'motion_prompt': '...'})."""
    return _out(_call("start", stage_key=stage_key, backend=backend, params=params))


@mcp.tool()
def af_poll(job: str, also_done: Optional[list] = None) -> str:
    """Poll a background job started with af_start. Returns status 'running' | 'done' |
    error. On 'done' the resulting state is persisted and the stage (plus any
    `also_done` stages, e.g. ['uv','texture'] for a combined generation) marked DONE."""
    return _out(_call("poll", jid=job, also_done=also_done))


@mcp.tool()
def af_animate_batch_start(clips: list) -> str:
    """BULK Kimodo animation generation. `clips` = a list of objects, each:
    {"name": str, "motion_prompt": str, "num_frames": int (optional, default 196),
     "playback": "once"|"loop"|"hold" (optional)}.
    Generates every clip sequentially on ONE warm Modal container (≈$0.05/clip vs
    ≈$0.22 standalone). Returns {"job": "<id>"}; poll with af_animate_batch_poll,
    then af_animate_batch_apply to retarget them onto the rig."""
    return _out(_call("start_batch", clips=clips))


@mcp.tool()
def af_animate_batch_poll(job: str) -> str:
    """Poll a bulk animation job: status, done/total progress, and per-clip
    result + generation seconds (for costing)."""
    return _out(_call("poll_batch", jid=job))


@mcp.tool()
def af_animate_batch_apply(job: str, armature_name: str = "") -> str:
    """Retarget all generated clips in the batch onto the rig as named, fake-user
    actions (run after af_animate_batch_poll reports done). `armature_name`
    auto-detected if omitted."""
    return _out(_call("apply_batch", jid=job, armature_name=armature_name))


@mcp.tool()
def af_bvh_to_fbx(bvh: str, fbx: str = "", global_scale: float = 0.01,
                 axis_forward: str = "Z", axis_up: str = "Y") -> str:
    """Convert ONE .bvh motion file to .fbx (vendored mcsantiago/bvh2fbx recipe, run in
    the open Blender inside a throwaway scene so the current scene is untouched). `fbx`
    defaults to the BVH path with a .fbx extension. `global_scale` 0.01 suits SOMA/Kimodo
    BVH (authored in cm); bvh2fbx's original default was 0.0001. Returns output path + frames.
    Use this to get Kimodo BVH animations into a Unity-importable format."""
    return _out(_call("bvh_to_fbx", bvh=bvh, fbx=fbx, global_scale=global_scale,
                      axis_forward=axis_forward, axis_up=axis_up))


@mcp.tool()
def af_bvh_to_fbx_bulk(src_dir: str = "", out_dir: str = "", paths: Optional[list] = None,
                      global_scale: float = 0.01, axis_forward: str = "Z",
                      axis_up: str = "Y") -> str:
    """BULK convert BVH->FBX. Provide `paths` (a list of .bvh files) OR `src_dir` (every
    *.bvh in that folder). `out_dir` defaults to each file's own folder. Same in-Blender,
    isolated-temp-scene conversion as af_bvh_to_fbx. Returns per-file results + converted/total count."""
    return _out(_call("bvh_to_fbx_bulk", src_dir=src_dir, out_dir=out_dir, paths=paths,
                      global_scale=global_scale, axis_forward=axis_forward, axis_up=axis_up))


@mcp.tool()
def af_bvh_to_fbx_combined(fbx: str, src_dir: str = "", paths: Optional[list] = None,
                          global_scale: float = 0.01) -> str:
    """Combine MANY BVH clips into ONE multi-clip FBX — a single shared skeleton with one
    named take per clip, which Unity imports as separate AnimationClips on one model.
    Provide `paths` (a list of .bvh files) OR `src_dir` (every *.bvh in it), plus the output
    `fbx` path. Runs a fresh headless Blender (vendored bvh2fbx/combine_fbx.py) so the take
    set is clean and your open Blender scene is untouched. `global_scale` 0.01 for SOMA/cm BVH."""
    return _out(_call("bvh_to_fbx_combined", fbx=fbx, src_dir=src_dir, paths=paths,
                      global_scale=global_scale))


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
