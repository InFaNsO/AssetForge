"""Full Blender registry: real generation + bpy geometry + texture + rigging backends.

Phase 3-5 additions:
  Stage 3: + Meshy, + Hunyuan3D (via fal.ai)
  Stage 7: TextureEnhanceBackend replaces TextureStub
  Stage 8: RigifyBackend + UniRigBackend replace RigStub
"""
from __future__ import annotations

from typing import Optional

from assetforge.core.adapter import BackendRegistry
from assetforge.core.backends.generation.copilot3d import Copilot3DBackend
from assetforge.core.backends.generation.drivers import BrowserDriver
from assetforge.core.backends.generation.hunyuan import FalHttpClient, HunyuanBackend
from assetforge.core.backends.generation.meshy import MeshyBackend, MeshyHttpClient
from assetforge.core.backends.generation.tripo import HttpClient, TripoBackend
from assetforge.core.backends.kimodo.kimodo import KimodoBackend
from assetforge.core.backends.meshy.animation import MeshyAnimationBackend
from assetforge.core.backends.meshy.retexture import MeshyRetextureBackend
from assetforge.core.backends.meshy.rigging import MeshyRiggingBackend
from assetforge.core.backends.remesh.meshy_remesh import MeshyRemeshBackend
from assetforge.core.backends.stubs import AnimateStub, ValidateStub

from .geometry.bake import BakeBackend
from .geometry.collision import CollisionBackend
from .geometry.export_ import ExportBackend
from .geometry.lod import LODBackend
from .geometry.retopo import RetopoBackend
from .geometry.uv import UVBackend
from .rigging.rigify_backend import RigifyBackend
from .rigging.unirig import UniRigBackend, UniRigHttpClient
from .texture.backend import TextureEnhanceBackend


def build_blender_registry(
    *,
    copilot_driver: Optional[BrowserDriver] = None,
    tripo_http: Optional[HttpClient] = None,
    meshy_http: Optional[MeshyHttpClient] = None,
    fal_http: Optional[FalHttpClient] = None,
    unirig_http: Optional[UniRigHttpClient] = None,
    meshy_stage_client=None,
    meshy_remesh_http=None,
    kimodo_url: Optional[str] = None,
) -> BackendRegistry:
    """Full Phase 3-5 registry for use inside Blender."""
    reg = BackendRegistry()

    # Stage 3: four generation backends — resolver picks by key + cost
    reg.register(Copilot3DBackend(driver=copilot_driver))  # free automation
    reg.register(TripoBackend(http_client=tripo_http))      # paid, quads
    reg.register(MeshyBackend(http_client=meshy_http))      # paid, PBR textures
    reg.register(HunyuanBackend(http_client=fal_http))      # paid, high quality

    # Stages 4-6: geometry algorithms
    reg.register(MeshyRemeshBackend(http_client=meshy_remesh_http))  # API, preferred
    reg.register(RetopoBackend())                                    # bpy fallback
    reg.register(UVBackend())
    reg.register(BakeBackend())

    # Stage 7: texture — Meshy retexture preferred, enhance pipeline fallback
    reg.register(MeshyRetextureBackend(client=meshy_stage_client))
    reg.register(TextureEnhanceBackend())

    # Stage 8: rigging — Meshy + UniRig preferred (ML quality), auto_rig fallback
    reg.register(MeshyRiggingBackend(client=meshy_stage_client))
    reg.register(UniRigBackend(http_client=unirig_http))
    reg.register(RigifyBackend())

    # Stages 9, 13: animation — Meshy library + Kimodo generative, stub fallback
    reg.register(MeshyAnimationBackend(client=meshy_stage_client))
    reg.register(KimodoBackend(api_url=kimodo_url))
    reg.register(AnimateStub())
    reg.register(ValidateStub())

    # Stages 10-12: geometry finalization
    reg.register(LODBackend())
    reg.register(CollisionBackend())
    reg.register(ExportBackend())

    return reg
