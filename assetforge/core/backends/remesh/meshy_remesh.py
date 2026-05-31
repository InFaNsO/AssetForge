"""Meshy Remesh API backend for stage 4 (retopology).

Meshy's proprietary quad remesher produces clean game-ready topology — it's the same
system that makes Meshy generation output look clean. Unlike open-source tools
(Instant Meshes, QuadriFlow) it is trained on game-asset meshes and handles
multi-piece characters with overlapping geometry reliably.

API flow  (base https://api.meshy.ai/openapi/v1, Bearer auth):
    POST /remesh   {model_url | input_task_id, topology, target_polycount}
                -> {result: task_id}
    GET  /remesh/{task_id}   (poll)
                -> {status, model_urls.glb}

Two input paths
    1. input_task_id — if the mesh was produced by Meshy generation in the same
       pipeline run, we pass the Meshy task ID directly.  No re-upload needed.
    2. model_url as data URI — for any other source (Copilot 3D, local GLB, etc.)
       we base64-encode the file and send it as
       ``data:application/octet-stream;base64,{b64}``.
       Meshy documents this as a supported model_url format.

Same API key as the Meshy generation backend (secret_name = 'meshy').
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

_BASE = "https://api.meshy.ai/openapi/v1"
_DONE = "SUCCEEDED"
_FAILED = {"FAILED"}
_MAX_DATA_URI_MB = 30   # Meshy's undocumented but practical limit


class MeshyRemeshError(RuntimeError):
    pass


class MeshyRemeshHttpClient(Protocol):
    def create_task(self, base_url: str, api_key: str, body: dict) -> dict: ...
    def get_task(self, base_url: str, api_key: str, task_id: str) -> dict: ...
    def download(self, url: str, dest: str) -> str: ...


class UrllibMeshyRemeshClient:
    def _post(self, url: str, api_key: str, body: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def _get(self, url: str, api_key: str) -> dict:
        req = urllib.request.Request(url, method="GET")
        req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())

    def create_task(self, base_url: str, api_key: str, body: dict) -> dict:
        return self._post(f"{base_url}/remesh", api_key, body)

    def get_task(self, base_url: str, api_key: str, task_id: str) -> dict:
        return self._get(f"{base_url}/remesh/{task_id}", api_key)

    def download(self, url: str, dest: str) -> str:
        os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
        urllib.request.urlretrieve(url, dest)
        return dest


def _glb_to_data_uri(path: str) -> str:
    size_mb = os.path.getsize(path) / 1_048_576
    if size_mb > _MAX_DATA_URI_MB:
        raise MeshyRemeshError(
            f"GLB is {size_mb:.1f} MB — too large for Meshy data URI upload "
            f"(limit ~{_MAX_DATA_URI_MB} MB). Use Meshy generation instead of "
            "Copilot 3D to get an input_task_id path.")
    with open(path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    return f"data:application/octet-stream;base64,{b64}"


class MeshyRemeshBackend(Backend):
    """Use Meshy's proprietary quad remesher as the retopo backend.

    Produces the same clean topology as Meshy generation output.
    Requires a Meshy API key (same key as the generation backend).
    """

    name = "meshy_remesh"
    stage = "retopo"
    secret_name = "meshy"

    def __init__(self, http_client: Optional[MeshyRemeshHttpClient] = None,
                 poll_interval: float = 3.0, timeout_s: float = 300.0) -> None:
        self.http = http_client or UrllibMeshyRemeshClient()
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("retopo", input_types=("mesh",), output_types=("mesh",),
                            emits_quads=True)

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=60.0, credits=2.0)

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        api_key = get_api_key(ctx.secrets, self.secret_name)
        if not api_key:
            raise MeshyRemeshError("no Meshy API key configured")

        from .._platform import platform_target
        target = params.get("target_polycount") or platform_target(
            params.get("platform", "indie"))

        # --- Build request body ---
        body: dict = {
            "topology": "quad",
            "target_polycount": target,
            "target_formats": ["glb"],
        }

        gen_meta = state.metadata.get("generation", {})
        if gen_meta.get("backend") == "meshy" and gen_meta.get("task_id"):
            # Best path: mesh came from Meshy — pass task ID directly, no re-upload
            body["input_task_id"] = gen_meta["task_id"]
            print(f"[AssetForge] Meshy Remesh: using input_task_id={gen_meta['task_id']}")
        else:
            # Data URI path: base64-encode the local GLB
            mesh_path = str(state.artifacts.get("mesh", ""))
            if not mesh_path or not os.path.exists(mesh_path):
                raise MeshyRemeshError(
                    "No local mesh found. Run the generate stage first.")
            body["model_url"] = _glb_to_data_uri(mesh_path)
            print(f"[AssetForge] Meshy Remesh: uploading GLB as data URI "
                  f"({os.path.getsize(mesh_path)/1_048_576:.1f} MB)")

        created = self.http.create_task(_BASE, api_key, body)
        task_id = created.get("result")
        if not task_id:
            raise MeshyRemeshError(f"remesh task creation failed: {created}")

        glb_url = self._poll(api_key, task_id)
        dest = os.path.join(ctx.work_dir, f"{state.id}_meshy_retopo.glb")
        self.http.download(glb_url, dest)

        state.artifacts["mesh"] = dest
        state.artifacts["topology"] = "quad"
        state.metadata.setdefault("retopo", {}).update({
            "method": "meshy_remesh",
            "task_id": task_id,
            "target_polycount": target,
        })
        print(f"[AssetForge] Meshy Remesh done -> {dest}")
        return state

    def _poll(self, api_key: str, task_id: str) -> str:
        deadline = time.monotonic() + self.timeout_s
        while True:
            data = self.http.get_task(_BASE, api_key, task_id)
            status = data.get("status", "")
            if status == _DONE:
                url = (data.get("model_urls") or {}).get("glb")
                if not url:
                    raise MeshyRemeshError(
                        f"SUCCEEDED but no GLB URL in response: {data}")
                return url
            if status in _FAILED:
                raise MeshyRemeshError(
                    f"Meshy remesh task {task_id} failed: "
                    f"{data.get('task_error', 'unknown error')}")
            if time.monotonic() > deadline:
                raise MeshyRemeshError(
                    f"Meshy remesh task {task_id} timed out (status={status})")
            time.sleep(self.poll_interval)
