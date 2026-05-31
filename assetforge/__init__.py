"""AssetForge — AI game-asset pipeline.

The :mod:`assetforge.core` subpackage is pure Python and must never import ``bpy``;
it is the engine that runs in CI without Blender. The Blender-only layer lives in
:mod:`assetforge.blender_addon`.
"""

__version__ = "0.1.0"
