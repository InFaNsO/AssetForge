"""Combine many BVH clips into ONE multi-clip FBX (one skeleton, one named take per clip).

Extends mcsantiago/bvh2fbx's headless approach. Run via a FRESH headless Blender so
bpy.data.actions starts empty — that guarantees `bake_anim_use_all_actions` exports
exactly our clips and nothing leaks in from another scene:

    blender -b --python combine_fbx.py -- OUT.fbx GLOBAL_SCALE clip1.bvh clip2.bvh ...

Each BVH becomes one Blender action named after its file; all actions are baked onto a
single shared armature (every clip shares the same SOMA skeleton, so the actions are
interchangeable). Unity imports the result as one model with N named AnimationClips.
"""
import bpy
import os
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
out_fbx = argv[0]
global_scale = float(argv[1])
bvhs = argv[2:]

# Fresh, empty file (drop the default cube/camera/light + any actions)
for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
for a in list(bpy.data.actions):
    bpy.data.actions.remove(a)

keep = None
clips = []
for f in bvhs:
    name = os.path.splitext(os.path.basename(f))[0]
    before = set(bpy.data.objects)
    bpy.ops.import_anim.bvh(
        filepath=f, filter_glob="*.bvh", global_scale=global_scale, frame_start=1,
        target='ARMATURE', use_fps_scale=False, use_cyclic=False, rotate_mode='NATIVE',
        axis_forward='Z', axis_up='Y', update_scene_fps=False, update_scene_duration=True)
    arm = next((o for o in bpy.data.objects if o not in before and o.type == 'ARMATURE'), None)
    if arm and arm.animation_data and arm.animation_data.action:
        arm.animation_data.action.name = name
        arm.animation_data.action.use_fake_user = True   # survive until export
        clips.append(name)
    if keep is None:
        keep = arm                 # first armature is the shared skeleton we export
    elif arm is not None:
        bpy.data.objects.remove(arm, do_unlink=True)   # extra rig discarded; its action stays

# Clean names so Unity shows tidy clips: strip the common filename prefix from each
# action ("cinderscale_walk" -> "walk"), and give the shared rig a neutral name based on
# that prefix instead of inheriting the first clip's name.
prefix = os.path.commonprefix([a.name for a in bpy.data.actions])
prefix = prefix[:prefix.rfind("_") + 1] if "_" in prefix else ""
for a in bpy.data.actions:
    if prefix and a.name.startswith(prefix):
        a.name = a.name[len(prefix):]
keep.name = prefix.rstrip("_") or "rig"

bpy.ops.object.select_all(action='DESELECT')
keep.select_set(True)
bpy.context.view_layer.objects.active = keep
keep.animation_data.action = None   # let bake_anim_use_all_actions drive every take

bpy.ops.export_scene.fbx(
    filepath=out_fbx, use_selection=True, apply_scale_options='FBX_SCALE_NONE',
    axis_forward='Z', axis_up='Y', add_leaf_bones=False,
    bake_anim=True, bake_anim_use_all_actions=True, bake_anim_use_nla_strips=False)

print("COMBINED_OK clips=%d -> %s" % (len(clips), out_fbx))
