"""Stage 9 — NVIDIA Kimodo generative animation backend.

Kimodo (KInematic MOtion DiffusiOn) generates 3D skeletal animations from text
prompts. This fills the gap left by Meshy Animation's library-only approach —
Kimodo creates NOVEL motion from a description.

  "person stumbling and catching themselves"
  "character doing a victory dance, excited and energetic"
  "aggressive combat kick sequence"

Runs locally via a Docker REST wrapper:
  docker run -p 9551:9551 \\
    -e HF_TOKEN=<your-huggingface-token> \\
    -e TEXT_ENCODER_DEVICE=cpu \\           # offloads Llama-3 to RAM (saves ~13 GB VRAM)
    --gpus=all \\
    ghcr.io/eyalenav/kimodo-api:latest
  POST http://localhost:9551/generate  {"prompt": "..."}  ->  NPZ binary

Hardware (RTX 4080 16 GB + 94 GB RAM):
  With TEXT_ENCODER_DEVICE=cpu: motion model ~3 GB VRAM, text encoder in RAM. Feasible.
  First run downloads ~16 GB (Llama-3-8B + Kimodo weights).

Output: NPZ file with SOMA skeleton animation (77 joints, SMPL-X-compatible for j<24).
The Blender addon converts NPZ -> Blender FCurves using SOMA joint index -> Mixamo
bone name mapping, then applies to the rigged character.

SOMA joint index -> Mixamo bone mapping (first 24 SMPL-X-compatible joints):
  0  pelvis       -> Hips
  1  left_hip     -> LeftUpLeg
  2  right_hip    -> RightUpLeg
  3  spine1       -> Spine
  4  left_knee    -> LeftLeg
  5  right_knee   -> RightLeg
  6  spine2       -> Spine1
  7  left_ankle   -> LeftFoot
  8  right_ankle  -> RightFoot
  9  spine3       -> Spine2
  10 left_foot    -> LeftToeBase
  11 right_foot   -> RightToeBase
  12 neck         -> Neck
  13 left_collar  -> LeftShoulder
  14 right_collar -> RightShoulder
  15 head         -> Head
  16 left_shoulder-> LeftArm
  17 right_shoulder->RightArm
  18 left_elbow   -> LeftForeArm
  19 right_elbow  -> RightForeArm
  20 left_wrist   -> LeftHand
  21 right_wrist  -> RightHand
"""
from __future__ import annotations

import io
import json
import os
import urllib.request
from typing import Optional

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState

_DEFAULT_URL = "http://localhost:9551"

# SOMA joint index (0-23) -> Mixamo bone name
SOMA_TO_MIXAMO: dict[int, str] = {
    # Bone names verified against Meshy rigged GLB (June 2026).
    # Meshy uses Spine01/Spine02 instead of Spine1/Spine2, and 'neck' (lowercase).
    0:  "Hips",
    1:  "LeftUpLeg",
    2:  "RightUpLeg",
    3:  "Spine",
    4:  "LeftLeg",
    5:  "RightLeg",
    6:  "Spine01",
    7:  "LeftFoot",
    8:  "RightFoot",
    9:  "Spine02",
    10: "LeftToeBase",
    11: "RightToeBase",
    12: "neck",
    13: "LeftShoulder",
    14: "RightShoulder",
    15: "Head",
    16: "LeftArm",
    17: "RightArm",
    18: "LeftForeArm",
    19: "RightForeArm",
    20: "LeftHand",
    21: "RightHand",
}


class KimodoError(RuntimeError):
    pass


class KimodoBackend(Backend):
    """Generative motion backend — text prompt -> Blender animation action."""

    name = "kimodo"
    stage = "animate"

    def __init__(self, api_url: Optional[str] = None) -> None:
        self.api_url = (api_url or _DEFAULT_URL).rstrip("/")

    def supports_local(self) -> bool:
        return True   # "local" = user's own Docker container

    def capabilities(self) -> Capabilities:
        return Capabilities("animate", input_types=("skeleton",),
                            output_types=("animations",))

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=30.0, credits=0.0)   # free, self-hosted

    def is_available(self, ctx: RunContext, mode: RunMode):
        url = _get_url(ctx)
        # Modal HTTPS cold starts can take 30-60 s; use a longer probe timeout
        # for remote URLs vs the local Docker container.
        timeout = 5 if url.startswith("http://localhost") else 15
        try:
            req = urllib.request.Request(f"{url}/health", method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    return True, f"Kimodo running at {url}"
        except Exception:
            pass
        is_modal = "modal.run" in url
        if is_modal:
            hint = (
                f"Kimodo Modal endpoint not responding at {url}. "
                "Deploy with: modal deploy assetforge/modal/kimodo_app.py  "
                "then set ASSETFORGE_KIMODO_URL to the printed URL."
            )
        else:
            hint = (
                f"Kimodo not reachable at {url}. "
                "Start it with: docker run -p 9551:9551 -e HF_TOKEN=<token> "
                "-e TEXT_ENCODER_DEVICE=cpu --gpus=all "
                "ghcr.io/eyalenav/kimodo-api:latest"
            )
        return False, hint

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        prompt = params.get("motion_prompt", "")
        if not prompt:
            raise KimodoError("params['motion_prompt'] is required for Kimodo generation")

        url = _get_url(ctx)
        npz_data = _call_kimodo(url, prompt)

        npz_path = os.path.join(ctx.work_dir, f"{state.id}_kimodo.npz")
        os.makedirs(ctx.work_dir, exist_ok=True)
        with open(npz_path, "wb") as fh:
            fh.write(npz_data)

        state.artifacts.setdefault("animations", {})["kimodo"] = npz_path
        state.metadata.setdefault("animate", {}).update({
            "kimodo_prompt": prompt,
            "kimodo_npz": npz_path,
        })
        print(f"[AssetForge] Kimodo: '{prompt[:60]}...' -> {npz_path}")
        return state


def _get_url(ctx: RunContext) -> str:
    """Read Kimodo URL from user_data if set, else use the default."""
    return ctx.user_data.get("kimodo_url", _DEFAULT_URL) if hasattr(ctx, "user_data") else _DEFAULT_URL


def _call_kimodo(base_url: str, prompt: str) -> bytes:
    body = json.dumps({"prompt": prompt}).encode()
    req = urllib.request.Request(
        f"{base_url}/generate", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    # Modal cold start + model load + generation can exceed 2 min on first call;
    # local Docker is fast, but using a generous timeout hurts nothing.
    is_modal = "modal.run" in base_url
    timeout = 600 if is_modal else 180
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        raise KimodoError(f"Kimodo returned HTTP {exc.code}: {exc.read().decode()}") from exc
    except Exception as exc:
        raise KimodoError(f"Kimodo request failed: {exc}") from exc


def npz_to_blender_action(npz_path: str, armature_obj, action_name: str = "Kimodo"):
    """Convert a Kimodo NPZ to a Blender animation action applied to *armature_obj*.

    Requires numpy (available in Blender 4.1) and bpy (must run inside Blender).
    Uses SOMA_TO_MIXAMO joint mapping for the first 24 body joints.
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise KimodoError("numpy is required for NPZ import") from exc

    import bpy
    from mathutils import Matrix

    data = np.load(npz_path)
    local_rots = data["local_rot_mats"]   # [T, J, 3, 3]
    root_pos   = data.get("root_positions", None)   # [T, 3] or None
    T, J = local_rots.shape[:2]
    fps = 30

    action = bpy.data.actions.new(name=action_name)
    action.use_fake_user = True   # keep action alive when next anim becomes active
    armature_obj.animation_data_create()
    armature_obj.animation_data.action = action

    arm = armature_obj.pose

    for joint_idx, bone_name in SOMA_TO_MIXAMO.items():
        if joint_idx >= J:
            continue
        bone = arm.bones.get(bone_name)
        if bone is None:
            continue
        bone.rotation_mode = "QUATERNION"

        for t in range(T):
            mat = Matrix(local_rots[t, joint_idx].tolist())
            quat = mat.to_quaternion()
            bone.rotation_quaternion = quat
            bone.keyframe_insert("rotation_quaternion", frame=t + 1)

    # Apply root translation if available
    if root_pos is not None:
        root_bone = arm.bones.get("Hips")
        if root_bone:
            root_bone.rotation_mode = "QUATERNION"
            for t in range(T):
                root_bone.location = root_pos[t].tolist()
                root_bone.keyframe_insert("location", frame=t + 1)

    # Set action frame range
    action.frame_range = (1, T)
    bpy.context.scene.frame_end = max(bpy.context.scene.frame_end, T)
    print(f"[AssetForge] Kimodo NPZ imported: {T} frames, "
          f"{len(SOMA_TO_MIXAMO)} bones mapped")
    return action
