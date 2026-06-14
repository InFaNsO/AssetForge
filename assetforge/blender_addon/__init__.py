"""The bpy layer: a thin shell over ``assetforge.core`` (DEVELOPMENT_PLAN.md §1).

``bl_info`` and the Blender entry points live in the top-level ``assetforge`` package;
this subpackage just wires up the preference, operator, and panel modules. Because the
addon installed in Blender IS the ``assetforge`` package, ``import assetforge.core`` and
``from . import ...`` both resolve with no sys.path manipulation.
"""
from __future__ import annotations

from . import prefs, operators, panel, handlers

_MODULES = (prefs, operators, panel, handlers)


def register() -> None:
    for m in _MODULES:
        m.register()


def unregister() -> None:
    for m in reversed(_MODULES):
        m.unregister()
