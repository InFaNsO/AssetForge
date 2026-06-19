"""
Constraint-based Kimodo -> rig retargeting.

Ported/adapted from the Kimodo_Blender_Bridge addon (github.com/lewdineer/Kimodo_Blender_Bridge),
retarget.py. Method:
  * On each target bone add a Copy Rotation constraint (WORLD space) -> the matching source bone.
  * The root bone additionally gets a Copy Location (optional here).
  * Bake the constraint-driven motion to keyframes with nla.bake(visual_keying=True,
    clear_constraints=True) so the rig ends up self-contained.

This is the native, robust form of the "world-space absolute match" we validated by hand.

NOTE: the source repo ships with no LICENSE file (all-rights-reserved by default). This copy is
for internal evaluation only; confirm licensing before shipping/redistributing.
"""
import bpy
import mathutils

CONSTRAINT_PREFIX = "KIMODO_"


def apply_copy_rotation_constraints(source_arm, target_arm, bone_pairs, root_loc_bone=None):
    """bone_pairs: list of (source_bone_name, target_bone_name). WORLD-space Copy Rotation."""
    source_arm.hide_viewport = False
    target_arm.hide_viewport = False
    applied = 0
    warnings = []
    for src_name, tgt_name in bone_pairs:
        tp = target_arm.pose.bones.get(tgt_name)
        if not tp:
            warnings.append(f"target bone '{tgt_name}' missing")
            continue
        if src_name not in source_arm.data.bones:
            warnings.append(f"source bone '{src_name}' missing")
            continue
        # clear our previous constraints on this bone
        for c in list(tp.constraints):
            if c.name.startswith(CONSTRAINT_PREFIX):
                tp.constraints.remove(c)

        if root_loc_bone and tgt_name == root_loc_bone:
            loc = tp.constraints.new("COPY_LOCATION")
            loc.name = CONSTRAINT_PREFIX + "Location"
            loc.target = source_arm
            loc.subtarget = src_name
            loc.use_offset = False

        rot = tp.constraints.new("COPY_ROTATION")
        rot.name = CONSTRAINT_PREFIX + "Rotation"
        rot.target = source_arm
        rot.subtarget = src_name
        rot.mix_mode = 'REPLACE'
        rot.owner_space = 'WORLD'
        rot.target_space = 'WORLD'
        applied += 1
    return applied, warnings


def remove_constraints(target_arm):
    removed = 0
    for pb in target_arm.pose.bones:
        for c in list(pb.constraints):
            if c.name.startswith(CONSTRAINT_PREFIX):
                pb.constraints.remove(c)
                removed += 1
    return removed


def bake_to_keyframes(target_arm, frame_start, frame_end, ctx_override=None):
    """Bake constraint-driven motion to keyframes (visual keying), then clear constraints."""
    def _run():
        bpy.ops.object.select_all(action='DESELECT')
        target_arm.select_set(True)
        bpy.context.view_layer.objects.active = target_arm
        bpy.ops.object.mode_set(mode='POSE')
        bpy.ops.pose.select_all(action='SELECT')
        bpy.ops.nla.bake(
            frame_start=frame_start, frame_end=frame_end,
            only_selected=False, visual_keying=True,
            clear_constraints=True, clear_parents=False,
            use_current_action=True, bake_types={'POSE'},
        )
        bpy.ops.object.mode_set(mode='OBJECT')

    if ctx_override:
        with bpy.context.temp_override(**ctx_override):
            _run()
    else:
        _run()
    return True
