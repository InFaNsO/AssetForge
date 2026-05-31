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

    instant_meshes_path: bpy.props.StringProperty(  # type: ignore
        name="Instant Meshes executable",
        description=(
            "Path to InstantMeshes.exe — the best automated retopo tool for characters. "
            "Download free from: "
            "https://instant-meshes.s3.eu-central-1.amazonaws.com/Release/instant-meshes-windows.zip"
        ),
        subtype="FILE_PATH",
        default="",
    )

    def draw(self, context):
        layout = self.layout

        # --- Retopology tools ---
        box = layout.box()
        box.label(text="Retopology", icon="MOD_REMESH")
        row = box.row()
        row.prop(self, "instant_meshes_path", text="Instant Meshes")
        if not self.instant_meshes_path:
            box.label(
                text="Not set — retopo will use Decimate (lower quality). "
                     "Download InstantMeshes.exe from the link above.",
                icon="INFO",
            )

        # --- API keys ---
        box = layout.box()
        box.label(text="Generation API keys (stored locally, never in repo)", icon="KEYINGSET")
        for _secret, (attr, label) in _KEY_FIELDS.items():
            box.prop(self, attr, text=label)
        box.prop(self, "fall_back_to_env")


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
