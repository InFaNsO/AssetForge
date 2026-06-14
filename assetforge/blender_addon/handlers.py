"""App handlers — quality-of-life fixes that run while AssetForge is enabled.

**af_autobind_slots** — Blender 4.4+ "slotted actions" require an action's *slot*
to be bound before the action actually drives the rig. The Action Editor does NOT
reliably bind the slot when you select an action, so the rig appears frozen on the
previous pose (a recurring trap with Meshy/Kimodo-imported actions whose slots carry
stale names like ``Armature.001``). This handler binds the first compatible slot
whenever an armature has an active action but no bound slot — so selecting any action
in the Action Editor "just works". Loop-safe via a re-entrancy guard, and
``@persistent`` so it survives opening other .blend files.
"""
from __future__ import annotations

import bpy
from bpy.app.handlers import persistent

_binding = False


@persistent
def af_autobind_slots(scene=None, depsgraph=None):
    global _binding
    if _binding:
        return
    for obj in bpy.data.objects:
        if obj.type != "ARMATURE" or not obj.animation_data:
            continue
        ad = obj.animation_data
        if not ad.action:
            continue
        slots = getattr(ad.action, "slots", None)
        # Rebind whenever the current slot doesn't belong to the current action.
        # Switching actions leaves action_slot pointing at the *previous* action's
        # slot (non-None but wrong), so checking `not ad.action_slot` alone misses it.
        slot_valid = (slots and len(slots) and
                      ad.action_slot is not None and
                      ad.action_slot in slots)
        if not slot_valid and slots and len(slots):
            _binding = True
            try:
                ad.action_slot = slots[0]
            except Exception:
                pass
            _binding = False


def _dedup(handler_list) -> None:
    for h in list(handler_list):
        if getattr(h, "__name__", "") == "af_autobind_slots":
            handler_list.remove(h)


def register() -> None:
    _dedup(bpy.app.handlers.depsgraph_update_post)   # fires on action selection
    _dedup(bpy.app.handlers.frame_change_pre)        # fires on scrub / playback
    bpy.app.handlers.depsgraph_update_post.append(af_autobind_slots)
    bpy.app.handlers.frame_change_pre.append(af_autobind_slots)


def unregister() -> None:
    _dedup(bpy.app.handlers.depsgraph_update_post)
    _dedup(bpy.app.handlers.frame_change_pre)
