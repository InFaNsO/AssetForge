"""Addon preferences = the secrets store (DEVELOPMENT_PLAN.md §2.2).

Keys are entered here, masked with ``subtype='PASSWORD'``, persisted by Blender to the
user's local config — never to the repo, asset state, or provenance. :class:`BlenderSecretStore`
adapts these prefs to the ``assetforge.core.secrets.SecretStore`` protocol so adapters
read keys the same way in Blender as in tests/CI.

Upgrade path (Option B): swap the body of ``get`` to read the OS keyring instead, with no
change to any adapter.
"""
from __future__ import annotations

import bpy

from assetforge.core.secrets import EnvSecretStore

ADDON_PKG = __package__.split(".")[0]  # "assetforge"

# Backend secret name -> (preference attribute, label)
_KEY_FIELDS = {
    "tripo": ("tripo_api_key", "Tripo API key"),
    "meshy": ("meshy_api_key", "Meshy API key"),
    "rodin": ("rodin_api_key", "Rodin API key"),
    "fal": ("fal_api_key", "fal.ai API key"),
    "replicate": ("replicate_api_key", "Replicate API key"),
}


class AssetForgePreferences(bpy.types.AddonPreferences):
    bl_idname = ADDON_PKG

    tripo_api_key: bpy.props.StringProperty(name="Tripo API key", subtype="PASSWORD")      # type: ignore
    meshy_api_key: bpy.props.StringProperty(name="Meshy API key", subtype="PASSWORD")      # type: ignore
    rodin_api_key: bpy.props.StringProperty(name="Rodin API key", subtype="PASSWORD")      # type: ignore
    fal_api_key: bpy.props.StringProperty(name="fal.ai API key", subtype="PASSWORD")        # type: ignore
    replicate_api_key: bpy.props.StringProperty(name="Replicate API key", subtype="PASSWORD")  # type: ignore

    fall_back_to_env: bpy.props.BoolProperty(  # type: ignore
        name="Fall back to environment variables",
        description="If a key field is blank, read ASSETFORGE_<NAME>_API_KEY from the environment",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text="API keys are stored locally and never written to assets or the repo.")
        for _secret, (attr, label) in _KEY_FIELDS.items():
            layout.prop(self, attr, text=label)
        layout.prop(self, "fall_back_to_env")


class BlenderSecretStore:
    """Adapts AddonPreferences to the core SecretStore protocol."""

    def __init__(self, prefs: "AssetForgePreferences") -> None:
        self._prefs = prefs
        self._env = EnvSecretStore() if getattr(prefs, "fall_back_to_env", False) else None

    def get(self, name: str):
        field = _KEY_FIELDS.get(name)
        if field:
            value = getattr(self._prefs, field[0], "") or ""
            if value:
                return value
        if self._env is not None:
            return self._env.get(name)
        return None


def get_secret_store(context) -> BlenderSecretStore:
    prefs = context.preferences.addons[ADDON_PKG].preferences
    return BlenderSecretStore(prefs)


def register() -> None:
    bpy.utils.register_class(AssetForgePreferences)


def unregister() -> None:
    bpy.utils.unregister_class(AssetForgePreferences)
