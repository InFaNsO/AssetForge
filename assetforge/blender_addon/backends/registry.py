"""Full Blender registry: real generation backends + bpy geometry algorithms.

This is what the addon operators use. ``assetforge.core.backends.registry`` (used in tests
and the CLI) only has generation + stubs; this one replaces stages 4-13 with real bpy ops.
"""
from __future__ import annotations

from typing import Optional

from assetforge.core.adapter import BackendRegistry
from assetforge.core.backends.generation.copilot3d import Copilot3DBackend
from assetforge.core.backends.generation.drivers import BrowserDriver
from assetforge.core.backends.generation.tripo import HttpClient, TripoBackend

from .geometry.bake import BakeBackend
from .geometry.collision import CollisionBackend
from .geometry.export_ import ExportBackend
from .geometry.lod import LODBackend
from .geometry.retopo import RetopoBackend
from .geometry.uv import UVBackend

# Stage 7 (texture), 8 (rig), 9 (animate), 13 (validate) are still stubs in Phase 2.
from assetforge.core.backends.stubs import (
    TextureStub, RigStub, AnimateStub, ValidateStub,
)


def build_blender_registry(
    *,
    copilot_driver: Optional[BrowserDriver] = None,
    tripo_http: Optional[HttpClient] = None,
) -> BackendRegistry:
    """Assemble the full Phase-2 registry for use inside Blender."""
    reg = BackendRegistry()

    # Stage 3: generation (same as Phase 1)
    reg.register(Copilot3DBackend(driver=copilot_driver))
    reg.register(TripoBackend(http_client=tripo_http))

    # Stages 4-6: geometry prep
    reg.register(RetopoBackend())
    reg.register(UVBackend())
    reg.register(BakeBackend())

    # Stages 10-12: game-ready finalization
    reg.register(LODBackend())
    reg.register(CollisionBackend())
    reg.register(ExportBackend())

    # Stages 7-9, 13: still stubs (Phase 4-6 will replace them)
    reg.register(TextureStub())
    reg.register(RigStub())
    reg.register(AnimateStub())
    reg.register(ValidateStub())

    return reg
