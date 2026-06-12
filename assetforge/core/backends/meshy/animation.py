"""Stage 9 — Meshy Animation backend (library clips).

Applies one or more of Meshy's 584+ pre-recorded mocap clips to a Meshy-rigged
character. Works as a pair with MeshyRiggingBackend — it reads the rig_task_id
stored in state.metadata["rig"]["task_id"].

Meshy Animation API (POST /openapi/v1/animations):
  Input:  rig_task_id, action_id (integer from the 584+ motion library)
  Output: animated GLB + FBX
  Cost:   ~2 credits per clip

Animation library categories (584+ motions):
  DailyActions, WalkAndRun, Fighting, Dancing, BodyMovements
  Full list: https://docs.meshy.ai/en/api/animation-library

Common action IDs (verify current IDs against the live API):
  Idle variants  ~  1-10
  Walking        ~ 11-30
  Running        ~ 31-50
  Fighting       ~ 200-300
  Dancing        ~ 400-500

This backend generates LIBRARY animations. For GENERATIVE (text-prompt) motion,
use KimodoBackend (stage 9 companion) — the two run independently, both storing
their results in state.artifacts["animations"].
"""
from __future__ import annotations

import os
from typing import Optional

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState
from ...secrets import get_api_key
from ._base import MeshyClient, MeshyError

# Curated default set: idle + walk + run.  User can override via params["action_ids"].
DEFAULT_ACTION_IDS = [1, 11, 31]   # approximate — verify from docs.meshy.ai/en/api/animation-library


class MeshyAnimationBackend(Backend):
    name = "meshy_animation"
    stage = "animate"
    secret_name = "meshy"

    def __init__(self, client: Optional[MeshyClient] = None,
                 poll_interval: float = 3.0, timeout_s: float = 180.0) -> None:
        self.client = client or MeshyClient()
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("animate", input_types=("skeleton",),
                            output_types=("animations",))

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        n = len(params.get("action_ids", DEFAULT_ACTION_IDS))
        return CostEstimate(seconds=60.0 * n, credits=2.0 * n)

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        api_key = get_api_key(ctx.secrets, self.secret_name)
        if not api_key:
            raise MeshyError("no Meshy API key configured")

        rig_task_id = state.metadata.get("rig", {}).get("task_id")
        if not rig_task_id:
            raise MeshyError(
                "No rig_task_id found in state — run Meshy Rigging (stage 8) first.")

        action_ids = list(params.get("action_ids", DEFAULT_ACTION_IDS))
        animations: dict = dict(state.artifacts.get("animations", {}))

        for action_id in action_ids:
            body = {
                "rig_task_id": rig_task_id,
                "action_id": int(action_id),
                "target_formats": ["glb"],
            }
            created = self.client.post("animations", api_key, body)
            task_id = created.get("result")
            if not task_id:
                print(f"[AssetForge] Meshy Animation: action {action_id} task creation failed")
                continue

            result = self.client.poll("animations", api_key, task_id,
                                       self.poll_interval, self.timeout_s)
            glb_url = (result.get("model_urls") or {}).get("glb")
            if not glb_url:
                print(f"[AssetForge] Meshy Animation: action {action_id} no GLB URL")
                continue

            dest = os.path.join(ctx.work_dir, f"{state.id}_anim_{action_id}.glb")
            self.client.download(glb_url, dest)
            animations[f"action_{action_id}"] = dest
            print(f"[AssetForge] Meshy Animation: action {action_id} -> {dest}")

        state.artifacts["animations"] = animations
        state.metadata.setdefault("animate", {}).update({
            "backend": self.name,
            "action_ids": action_ids,
            "count": len(animations),
        })
        return state
