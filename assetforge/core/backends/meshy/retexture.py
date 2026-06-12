"""Stage 7 — Meshy Retexture backend.

Applies AI-generated PBR textures to an existing 3D mesh using a text style prompt
or reference image. Replaces AssetForge's algorithmic delight+PBR decomp pipeline
as the primary texturing method.

Meshy Retexture API (POST /openapi/v1/retexture):
  Input:  model_url or input_task_id
          text_style_prompt  OR  image_style_url
          enable_pbr (bool) — also generate roughness/metallic/normal maps
          hd_texture (bool) — 4K output
  Output: textured GLB/FBX + PBR map URLs
  Cost:   ~10 credits
"""
from __future__ import annotations

import os
from typing import Optional

from ...adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from ...asset_state import AssetState
from ...secrets import get_api_key
from ._base import MeshyClient, MeshyError


class MeshyRetextureBackend(Backend):
    name = "meshy_retexture"
    stage = "texture"
    secret_name = "meshy"

    def __init__(self, client: Optional[MeshyClient] = None,
                 poll_interval: float = 3.0, timeout_s: float = 300.0) -> None:
        self.client = client or MeshyClient()
        self.poll_interval = poll_interval
        self.timeout_s = timeout_s

    def supports_api(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("texture", input_types=("mesh",), output_types=("textures",))

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=90.0, credits=10.0)

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        api_key = get_api_key(ctx.secrets, self.secret_name)
        if not api_key:
            raise MeshyError("no Meshy API key configured")

        body: dict = {
            "enable_pbr": True,
            "hd_texture": params.get("hd_texture", False),
            "target_formats": ["glb"],
        }

        # Style: text prompt takes priority over image reference
        style_prompt = params.get("style_prompt", "")
        style_image = params.get("style_image_url", "")
        if style_prompt:
            body["text_style_prompt"] = style_prompt
        elif style_image:
            body["image_style_url"] = style_image
        else:
            # Default: re-texture in a game-ready PBR style
            body["text_style_prompt"] = "game-ready PBR material, realistic textures"

        # Prefer task ID from a Meshy generation run (no re-upload needed)
        gen_task_id = state.metadata.get("generation", {}).get("task_id")
        if state.metadata.get("generation", {}).get("backend") == "meshy" and gen_task_id:
            body["input_task_id"] = gen_task_id
        else:
            mesh_path = str(state.artifacts.get("mesh", ""))
            if not mesh_path or not os.path.exists(mesh_path):
                raise MeshyError("no mesh artifact found — run generate stage first")
            body["model_url"] = _to_data_uri(mesh_path)

        created = self.client.post("retexture", api_key, body)
        task_id = created.get("result")
        if not task_id:
            raise MeshyError(f"retexture task creation failed: {created}")

        result = self.client.poll("retexture", api_key, task_id,
                                   self.poll_interval, self.timeout_s)

        glb_url = (result.get("model_urls") or {}).get("glb")
        if not glb_url:
            raise MeshyError(f"retexture succeeded but no GLB URL: {result}")

        dest = os.path.join(ctx.work_dir, f"{state.id}_retextured.glb")
        self.client.download(glb_url, dest)
        state.artifacts["mesh"] = dest

        # Store PBR map URLs as texture artifacts
        textures = {}
        for role in ("base_color", "metallic", "roughness", "normal", "emission"):
            url = (result.get("texture_urls") or {}).get(role)
            if url:
                tex_dest = os.path.join(ctx.work_dir, f"{state.id}_{role}.png")
                self.client.download(url, tex_dest)
                textures[role.replace("_", "")] = tex_dest
        state.artifacts["textures"] = textures

        state.metadata.setdefault("texture", {}).update({
            "backend": self.name,
            "task_id": task_id,
            "style_prompt": style_prompt,
        })
        print(f"[AssetForge] Meshy Retexture done -> {dest}")
        return state


def _to_data_uri(path: str) -> str:
    import base64
    with open(path, "rb") as fh:
        return f"data:application/octet-stream;base64,{base64.b64encode(fh.read()).decode()}"
