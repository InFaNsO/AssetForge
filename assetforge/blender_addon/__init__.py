"""AssetForge Blender addon entry point.

This is the only part of the project that imports ``bpy``. It is a thin shell over
``assetforge.core`` (DEVELOPMENT_PLAN.md §1: "operators exist from Phase 1, UI is a thin
shell over them"). The full guided stage-rail UX is Phase 8; this is the minimal panel.

Install: zip/point Blender at the ``assetforge`` folder, or symlink it into
``scripts/addons``. We add the repo root to ``sys.path`` so ``import assetforge.core``
resolves regardless of how the addon is installed.
"""
from __future__ import annotations

import os
import sys

bl_info = {
    "name": "AssetForge",
    "author": "AssetForge",
    "version": (0, 1, 0),
    "blender": (4, 1, 0),
    "location": "View3D > N-panel > AssetForge",
    "description": "AI game-asset pipeline: image/text/mesh -> game-ready export.",
    "category": "Pipeline",
}

# Make the repo root importable so `assetforge.core` resolves when run as an addon.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)

from . import prefs, operators, panel  # noqa: E402  (after sys.path setup)

_MODULES = (prefs, operators, panel)


def register() -> None:
    for m in _MODULES:
        m.register()


def unregister() -> None:
    for m in reversed(_MODULES):
        m.unregister()
