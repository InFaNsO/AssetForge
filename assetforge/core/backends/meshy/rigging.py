"""Stage 8 — Meshy Rigging backend.

Auto-rigs a humanoid mesh using Meshy's AI. Output uses a Mixamo-compatible
skeleton, which works directly with Meshy's Animation API and Kimodo retargeting.

Meshy Rigging API (POST /openapi/v1/rigging):
  Input:  model_url (textured GLB) or input_task_id
          height_meters (default 1.7)
  Output: rigged_character_glb_url, rigged_character_fbx_url
          basic_animations (walk + run, FBX + GLB)
  Cost:   ~5 credits

Prerequisites (Meshy's requirements):
  - Humanoid bipedal mesh (facing +Z axis)
  - Must be textured (UV-mapped with texture image)
  - Max 300k faces
  - GLB format

The rig_task_id stored in metadata is used by MeshyAnimationBackend in stage 9.
"""
from __future__ import annotations

import os
from typing import Optional

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState
from ...secrets import get_api_key
from ._base import MeshyClient, MeshyError


class MeshyRiggingBackend(Backend):
    name = "meshy_rigging"
    stage = "rig"
    secret_name = "meshy"

    def __init__(self, client: Optional[MeshyClient] = None,
                 poll_interval: float = 3.0, timeout_s: float = 300.0) -> None:
        self.client = client or MeshyClient()
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("rig", input_types=("mesh",), output_types=("skeleton",),
                            skeleton="mixamo")

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=120.0, credits=5.0)

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        api_key = get_api_key(ctx.secrets, self.secret_name)
        if not api_key:
            raise MeshyError("no Meshy API key configured")

        body: dict = {
            "height_meters": float(params.get("height_meters", 1.7)),
        }

        # Input priority:
        # 1. Retexture task_id (Meshy retexture output is already a clean textured mesh)
        # 2. Retopo'd local GLB — if Meshy remesh was done, that mesh is what we want to rig;
        #    bypass the generation task_id since Meshy rigging rejects generation tasks that
        #    have had their topology changed by a subsequent remesh call.
        # 3. Generation task_id (only when no retopo has been done)
        # 4. Any other local mesh path
        retex_id = state.metadata.get("texture", {}).get("task_id")
        retopo_done = state.metadata.get("retopo", {}).get("method") == "meshy_remesh"
        gen_id = state.metadata.get("generation", {}).get("task_id")
        mesh_path = str(state.artifacts.get("mesh", ""))

        if retex_id:
            body["input_task_id"] = retex_id
        elif retopo_done and mesh_path and os.path.exists(mesh_path):
            # Upload the retopo'd GLB directly — it is the mesh we intend to rig
            print(f"[AssetForge] Meshy Rigging: uploading retopo'd GLB as data URI")
            body["model_url"] = _to_data_uri(mesh_path)
        elif (state.metadata.get("generation", {}).get("backend") == "meshy" and gen_id):
            body["input_task_id"] = gen_id
        else:
            if not mesh_path or not os.path.exists(mesh_path):
                raise MeshyError("no mesh artifact — run generate + texture stages first")
            body["model_url"] = _to_data_uri(mesh_path)

        created = self.client.post("rigging", api_key, body)
        task_id = created.get("result")
        if not task_id:
            raise MeshyError(f"rigging task creation failed: {created}")

        task_data = self.client.poll("rigging", api_key, task_id,
                                     self.poll_interval, self.timeout_s)
        # Meshy task response wraps output under a nested "result" key
        task_result = task_data.get("result") or {}

        glb_url = task_result.get("rigged_character_glb_url")
        if not glb_url:
            raise MeshyError(f"rigging succeeded but no GLB URL: {task_data}")

        dest = os.path.join(ctx.work_dir, f"{state.id}_rigged.glb")
        self.client.download(glb_url, dest)
        state.artifacts["mesh"] = dest
        state.artifacts["skeleton"] = "mixamo"

        # Basic animations: flat dict with keys like walking_glb_url / running_glb_url.
        # Skip armature-only exports (walking_armature_glb_url) — we only want skinned GLBs.
        basic = task_result.get("basic_animations", {})
        anims = {}
        for key, url in basic.items():
            if key.endswith("_glb_url") and "_armature_" not in key and url:
                motion = key[: -len("_glb_url")]   # "walking_glb_url" -> "walking"
                adest = os.path.join(ctx.work_dir, f"{state.id}_anim_{motion}.glb")
                self.client.download(url, adest)
                anims[motion] = adest
        if anims:
            state.artifacts.setdefault("animations", {}).update(anims)

        state.metadata.setdefault("rig", {}).update({
            "backend": self.name,
            "task_id": task_id,          # used by MeshyAnimationBackend
            "skeleton": "mixamo",
        })
        print(f"[AssetForge] Meshy Rigging done -> {dest} (task={task_id})")
        return state


def _to_data_uri(path: str) -> str:
    import base64
    with open(path, "rb") as fh:
        return f"data:application/octet-stream;base64,{base64.b64encode(fh.read()).decode()}"
