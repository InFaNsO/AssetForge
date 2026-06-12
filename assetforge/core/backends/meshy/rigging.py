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

        # Input: prefer Meshy task ID from retexture or generation
        retex_id = state.metadata.get("texture", {}).get("task_id")
        gen_id = state.metadata.get("generation", {}).get("task_id")
        if retex_id:
            body["input_task_id"] = retex_id
        elif (state.metadata.get("generation", {}).get("backend") == "meshy" and gen_id):
            body["input_task_id"] = gen_id
        else:
            mesh_path = str(state.artifacts.get("mesh", ""))
            if not mesh_path or not os.path.exists(mesh_path):
                raise MeshyError("no mesh artifact — run generate + texture stages first")
            body["model_url"] = _to_data_uri(mesh_path)

        created = self.client.post("rigging", api_key, body)
        task_id = created.get("result")
        if not task_id:
            raise MeshyError(f"rigging task creation failed: {created}")

        result = self.client.poll("rigging", api_key, task_id,
                                   self.poll_interval, self.timeout_s)

        glb_url = result.get("rigged_character_glb_url")
        if not glb_url:
            raise MeshyError(f"rigging succeeded but no GLB URL: {result}")

        dest = os.path.join(ctx.work_dir, f"{state.id}_rigged.glb")
        self.client.download(glb_url, dest)
        state.artifacts["mesh"] = dest
        state.artifacts["skeleton"] = "mixamo"

        # Also download the included walk/run animations
        basic = result.get("basic_animations", {})
        anims = {}
        for motion, formats in basic.items():
            glb = (formats or {}).get("glb")
            if glb:
                adest = os.path.join(ctx.work_dir, f"{state.id}_anim_{motion}.glb")
                self.client.download(glb, adest)
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
