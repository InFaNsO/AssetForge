"""Stage 3 — Meshy image-to-3D generation backend (paid REST API).

Meshy v2 flow  (base https://api.meshy.ai/v2, Bearer auth):
    POST /image-to-3d   {image_url, enable_pbr}  ->  {result: task_id}
    GET  /image-to-3d/{task_id}                  ->  {status, model_urls.glb}

Status values: PENDING | IN_PROGRESS | SUCCEEDED | FAILED | EXPIRED
Output:        model_urls.glb -> download URL

Verify field names at https://docs.meshy.ai when you add a live key.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from typing import Optional, Protocol

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState
from ...secrets import get_api_key

BASE_URL = "https://api.meshy.ai/openapi/v1"
_DONE = "SUCCEEDED"
_FAILED = {"FAILED", "EXPIRED", "CANCELED"}


class MeshyHttpClient(Protocol):
    def create_task(self, base_url: str, api_key: str, image_path: str, params: dict) -> dict: ...
    def get_task(self, base_url: str, api_key: str, task_id: str) -> dict: ...
    def download(self, url: str, dest: str) -> str: ...


class MeshyError(RuntimeError):
    pass


class UrllibMeshyClient:
    """Real HTTP via stdlib urllib — no extra dependencies."""

    def _post(self, url: str, api_key: str, body: dict) -> dict:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def _get(self, url: str, api_key: str) -> dict:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def create_task(self, base_url: str, api_key: str, image_path: str, params: dict) -> dict:
        with open(image_path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
        ext = os.path.splitext(image_path)[1].lstrip(".").lower() or "png"
        if ext == "jpg":
            ext = "jpeg"
        meshy = dict(params.get("meshy", {}))            # caller overrides (copy; originals intact)
        should_texture = bool(meshy.pop("should_texture", True))
        body = {
            "image_url": f"data:image/{ext};base64,{b64}",
            "ai_model": meshy.pop("ai_model", "meshy-6"),         # Meshy 6 default; "meshy-5" for bg assets
            "should_texture": should_texture,                     # False -> geometry-only ("separate" mode)
            "enable_pbr": bool(meshy.pop("enable_pbr", True)) and should_texture,
            "pose_mode": meshy.pop("pose_mode", "a-pose"),        # riggable default for humanoids
            "topology": meshy.pop("topology", "triangle"),
            "target_polycount": int(meshy.pop("target_polycount", 30000)),
        }
        body.update(meshy)   # remaining overrides: hd_texture, texture_prompt, model_type, ...
        return self._post(f"{base_url}/image-to-3d", api_key, body)

    def get_task(self, base_url: str, api_key: str, task_id: str) -> dict:
        return self._get(f"{base_url}/image-to-3d/{task_id}", api_key)

    def download(self, url: str, dest: str) -> str:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        return dest


class MeshyBackend(Backend):
    name = "meshy"
    stage = "generate"
    secret_name = "meshy"

    def __init__(self, http_client: Optional[MeshyHttpClient] = None,
                 poll_interval: float = 3.0, timeout_s: float = 300.0) -> None:
        self.http = http_client or UrllibMeshyClient()
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("generate", input_types=("image",),
                            output_types=("mesh",), emits_quads=True)

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=60.0, credits=4.0)

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        api_key = get_api_key(ctx.secrets, self.secret_name)
        if not api_key:
            raise MeshyError("no Meshy API key configured")

        created = self.http.create_task(BASE_URL, api_key, state.source_ref, params)
        task_id = created.get("result")
        if not task_id:
            raise MeshyError(f"task creation returned no task id: {created}")

        glb_url = self._poll(api_key, task_id)
        dest = os.path.join(ctx.work_dir, f"{state.id}_meshy.glb")
        self.http.download(glb_url, dest)

        meshy_params = params.get("meshy", {})
        state.artifacts["mesh"] = dest
        state.metadata.setdefault("generation", {}).update(
            {"backend": self.name, "task_id": task_id,
             "ai_model": meshy_params.get("ai_model", "meshy-6"),
             "textured": meshy_params.get("should_texture", True)})
        return state

    def _poll(self, api_key: str, task_id: str) -> str:
        deadline = time.monotonic() + self.timeout_s
        while True:
            data = self.http.get_task(BASE_URL, api_key, task_id)
            status = data.get("status", "")
            if status == _DONE:
                url = (data.get("model_urls") or {}).get("glb")
                if not url:
                    raise MeshyError(f"SUCCEEDED but no GLB URL: {data}")
                return url
            if status in _FAILED:
                raise MeshyError(f"Meshy task {task_id} {status}")
            if time.monotonic() > deadline:
                raise MeshyError(f"Meshy task {task_id} timed out (status={status})")
            time.sleep(self.poll_interval)
