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
    # Indices from SOMASkeleton77.bone_order_names (verified 2026-06-14).
    # SOMA77 ordering: spine chain first, then arms+hands, then legs at end.
    # Completely different from SMPL-X (0-21 body joints) — do not confuse.
    #
    # Meshy spine naming is REVERSE-numbered vs standard Mixamo:
    #   Meshy "Spine02" = lowest vertebra (parent = Hips)
    #   Meshy "Spine01" = middle vertebra
    #   Meshy "Spine"   = chest/top vertebra (parent of LeftShoulder, connects to neck)
    # Verified 2026-06-15 from bone.parent checks on both Azureheart and husk rigs.
    0:  "Hips",
    1:  "Spine02",       # SOMA first vertebra above pelvis → Meshy Spine02 (bottom, parent=Hips)
    2:  "Spine01",       # SOMA second vertebra → Meshy Spine01 (middle)
    3:  "Spine",         # SOMA chest → Meshy Spine (top, parent of shoulders + neck)
    4:  "neck",          # Neck1  → neck  (Mixamo has one neck bone)
    6:  "Head",          # Head
    11: "LeftShoulder",
    12: "LeftArm",
    13: "LeftForeArm",
    14: "LeftHand",
    39: "RightShoulder",
    40: "RightArm",
    41: "RightForeArm",
    42: "RightHand",
    67: "LeftUpLeg",     # LeftLeg  → LeftUpLeg  (thigh)
    68: "LeftLeg",       # LeftShin → LeftLeg    (shin)
    69: "LeftFoot",
    70: "LeftToeBase",
    72: "RightUpLeg",    # RightLeg  → RightUpLeg
    73: "RightLeg",      # RightShin → RightLeg
    74: "RightFoot",
    75: "RightToeBase",
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
        npz_data = _call_kimodo(url, prompt, int(params.get("num_frames", 196)))

        npz_path = os.path.join(ctx.work_dir, f"{state.id}_kimodo.npz")
        os.makedirs(ctx.work_dir, exist_ok=True)
        with open(npz_path, "wb") as fh:
            fh.write(npz_data)

        if not isinstance(state.artifacts.get("animations"), dict):
            state.artifacts["animations"] = {}
        state.artifacts["animations"]["kimodo"] = npz_path
        state.metadata.setdefault("animate", {}).update({
            "kimodo_prompt": prompt,
            "kimodo_npz": npz_path,
        })
        print(f"[AssetForge] Kimodo: '{prompt[:60]}...' -> {npz_path}")
        return state


def _get_url(ctx: RunContext) -> str:
    """Read Kimodo URL from user_data if set, else use the default."""
    return ctx.user_data.get("kimodo_url", _DEFAULT_URL) if hasattr(ctx, "user_data") else _DEFAULT_URL


def _call_kimodo(base_url: str, prompt: str, num_frames: int = 196) -> bytes:
    """Call the Kimodo API, handling Modal's POST→303→poll-GET→303→... pattern.

    Modal ASGI deployment flow:
      1. POST /generate → 303 See Other → Location: /generate?__modal_function_call_id=X
      2. GET that URL → 303 again while computing (follow to new/same URL)
      3. Eventually GET returns 200 with the NPZ binary
    We block in the poll loop until data arrives or the deadline is reached.
    """
    import time as _t

    body = json.dumps({"prompt": prompt, "num_frames": int(num_frames)}).encode()
    is_modal = "modal.run" in base_url
    deadline = _t.time() + (1800 if is_modal else 180)

    # Opener that stops at any redirect so we can handle it ourselves
    class _StopRedirect(urllib.request.HTTPRedirectHandler):
        def _stop(self, req, fp, code, msg, headers):
            raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)
        http_error_301 = http_error_302 = http_error_303 = _stop
        http_error_307 = http_error_308 = _stop

    opener = urllib.request.build_opener(_StopRedirect())

    # ── Step 1: POST /generate ──────────────────────────────────────────────
    post_req = urllib.request.Request(f"{base_url}/generate", data=body, method="POST")
    post_req.add_header("Content-Type", "application/json")
    # Generous timeout for the POST — Modal may need minutes to spin up a container
    post_timeout = 600 if is_modal else 60

    poll_url = None
    try:
        with opener.open(post_req, timeout=post_timeout) as resp:
            return resp.read()   # local Docker returns NPZ directly
    except urllib.error.HTTPError as exc:
        loc = exc.headers.get("Location")
        if exc.code in (301, 302, 303, 307, 308) and loc:
            poll_url = loc if loc.startswith("http") else f"{base_url}{loc}"
            print(f"[AssetForge] Kimodo: POST→{exc.code}, polling {poll_url[:100]}")
        else:
            raise KimodoError(
                f"Kimodo POST /generate HTTP {exc.code} "
                f"(no redirect): {exc.read().decode()[:200]}"
            ) from exc
    except Exception as exc:
        raise KimodoError(f"Kimodo POST failed: {exc}") from exc

    # ── Step 2: poll loop ───────────────────────────────────────────────────
    # Modal returns 303 from the GET while computation is in progress.
    # Each 303 gives a new (or same) URL to retry. When computation is done
    # the GET returns 200 with the NPZ binary.
    # Short per-request timeout so we retry on socket inactivity.
    per_req_timeout = 60

    while _t.time() < deadline:
        try:
            get_req = urllib.request.Request(poll_url, method="GET")
            with opener.open(get_req, timeout=per_req_timeout) as resp:
                data = resp.read()
                print(f"[AssetForge] Kimodo: result {resp.status} len={len(data)} bytes")
                return data
        except urllib.error.HTTPError as exc:
            loc = exc.headers.get("Location")
            if exc.code in (301, 302, 303, 307, 308):
                if loc:
                    new_url = loc if loc.startswith("http") else f"{base_url}{loc}"
                    if new_url != poll_url:
                        print(f"[AssetForge] Kimodo: redirect → {new_url[:100]}")
                        poll_url = new_url
                _t.sleep(3)   # brief pause then retry
            else:
                raise KimodoError(
                    f"Kimodo poll HTTP {exc.code}: {exc.read().decode()[:200]}"
                ) from exc
        except OSError:
            _t.sleep(3)   # socket timeout / transient error, retry

    raise KimodoError(
        f"Kimodo timed out after {1800 if is_modal else 180}s — "
        "check Modal logs at modal.com/apps"
    )


def npz_to_blender_action(npz_path: str, armature_obj, action_name: str = "Kimodo",
                          world_align=None, apply_root: bool = False):
    """Convert a Kimodo NPZ to a Blender action, retargeted onto *armature_obj*.

    Re-expresses each SOMA local rotation in the target bone's own rest frame:

        basis[b] = A_b⁻¹ · (W · R_local[b] · W⁻¹) · A_b

    where A_b = bone rest orientation in armature space (bone.matrix_local.to_3x3()),
    R_local[b] = SOMA parent-relative local rotation for the mapped joint,
    W = SOMA Y-up → Blender Z-up world alignment (−90° rotation about X).

    The conjugation by A_b ensures that the SOMA T-pose (all R_local = identity)
    maps to the Meshy rig's rest pose (basis = identity → pb.matrix = A_b). Any
    remaining mismatch between SOMA and Meshy rest orientations is absorbed per-bone
    rather than compounding through the FK chain.

    SOMA77 joint-index mapping: verified 2026-06-14 from SOMASkeleton77.bone_order_names.
    Spine chain 0–10, left arm+fingers 11–38, right arm+fingers 39–66, legs 67–76.

    Args:
        world_align: optional mathutils.Matrix (3×3) overriding the default alignment.
        apply_root: if True, bake root translation onto the Hips bone (off by default).

    Requires numpy and bpy (must run inside Blender).
    """
    try:
        import numpy as np
    except ImportError as exc:
        raise KimodoError("numpy is required for NPZ import") from exc

    import math
    import bpy
    from mathutils import Matrix, Vector

    W = world_align or Matrix.Rotation(math.radians(-90), 3, "X")
    Winv = W.transposed()

    data = np.load(npz_path, allow_pickle=True)
    local_rots = data["local_rot_mats"]
    if local_rots.ndim == 5:          # strip batch dim: (1, T, J, 3, 3) → (T, J, 3, 3)
        local_rots = local_rots[0]
    T, J = local_rots.shape[:2]

    rest = armature_obj.data.bones
    A = {}
    for joint_idx, bone_name in SOMA_TO_MIXAMO.items():
        b = rest.get(bone_name)
        if b is not None:
            A[bone_name] = b.matrix_local.to_3x3()   # rest orientation in armature space

    action = bpy.data.actions.new(name=action_name)
    action.use_fake_user = True
    armature_obj.animation_data_create()
    armature_obj.animation_data.action = action

    pose = armature_obj.pose
    for joint_idx, bone_name in SOMA_TO_MIXAMO.items():
        if joint_idx >= J or bone_name not in A:
            continue
        pb = pose.bones.get(bone_name)
        if pb is None:
            continue
        pb.rotation_mode = "QUATERNION"
        Ab   = A[bone_name]
        Ainv = Ab.inverted()
        for t in range(T):
            R     = Matrix(local_rots[t, joint_idx].tolist())
            basis = Ainv @ (W @ R @ Winv) @ Ab
            pb.rotation_quaternion = basis.to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=t + 1)

    if apply_root:
        root_pos = data.get("root_positions", None)
        if root_pos is not None:
            if root_pos.ndim == 3:
                root_pos = root_pos[0]
            hips = pose.bones.get("Hips")
            if hips is not None:
                # Root motion is frame-0 relative so the rig doesn't teleport to the
                # SOMA source origin on frame 1: X_root(t) = X_pelvis(t) − X_pelvis(0).
                # The world-space delta is then mapped into the Hips rest basis, since
                # a pose bone's location is expressed in its own local axes.
                hips_basis_inv = rest["Hips"].matrix_local.to_3x3().inverted()
                p0 = Vector(root_pos[0].tolist())
                for t in range(T):
                    d_world = W @ (Vector(root_pos[t].tolist()) - p0)
                    hips.location = hips_basis_inv @ d_world
                    hips.keyframe_insert("location", frame=t + 1)

    action.frame_range = (1, T)
    bpy.context.scene.frame_end = max(bpy.context.scene.frame_end, T)
    print(f"[AssetForge] Kimodo NPZ retargeted: {T} frames, {len(A)} bones "
          f"(apply_root={apply_root})")
    return action
