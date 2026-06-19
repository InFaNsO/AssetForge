"""SOMA (Kimodo BVH) -> Meshy/Mixamo armature retarget — step 3 of the Kimodo path.

Background
----------
Kimodo emits motion on its 77-joint SOMA skeleton, whose rest pose is a T-pose.
Meshy rigs the character on a 24-bone Mixamo skeleton whose rest is an A-pose.
The old approach copied SOMA *local* rotations onto the Mixamo bones with a
conjugation that silently assumed both skeletons shared a rest pose. They don't
(T-pose vs A-pose), so arms splayed ~30-70 deg and the error was asymmetric L/R.

The robust path (validated against kimodo's own BVH output) is three steps:

  1. NPZ  -> BVH        kimodo's own exporter (rest-correct: from_standard_tpose)
  2. BVH  -> SOMA rig   Blender's bvh importer builds a rig whose rest == kimodo
                        neutral and drops the local rotations straight on (native).
  3. SOMA rig -> Meshy  THIS MODULE. Copy the SOMA rig's per-frame *world* bone
                        orientations onto the Meshy rig.

Why step 3 is correct where the old math failed
------------------------------------------------
It matches *absolute world orientation* per bone, not local deltas, so the
T-pose/A-pose rest gap never enters. Bone-length differences don't distort the
pose (only orientation is copied, never position), and roll/twist transfers for
free because the full 3x3 orientation is matched (no separate leaf-roll fix).

For a Meshy bone ``b`` mapped to SOMA bone ``s``, we want the Meshy bone's world
orientation to equal ``S[b]`` (the SOMA bone's world orientation). Blender stores
animation as each bone's local pose rotation ``basis``; solving Blender's FK
relation ``World[b] = World[parent] . (Rrest[parent]^-1 . Rrest[b]) . basis[b]``
for ``basis`` gives:

    basis[b] = Rrest[b]^-1 . Rrest[parent] . S[parent]^-1 . S[b]

where ``Rrest`` are the Meshy bone *armature-space* rest orientations
(``bone.matrix_local``) and ``S`` are *world* orientations of the source bones.
The Meshy object's own world rotation cancels in ``S[parent]^-1 . S[b]``, so this
is correct even if either rig is moved or rotated.

Requires bpy (runs inside Blender).
"""
from __future__ import annotations

from typing import Optional


# Meshy/Mixamo bone -> SOMA (somaskel77 / Kimodo BVH) bone.
# SOMA spine names (Spine1/Spine2/Chest, Neck1) differ from Meshy's reverse-numbered
# Spine02/Spine01/Spine + single 'neck'. SOMA 'LeftLeg' is the thigh; 'LeftShin' the calf.
MESHY_TO_SOMA: dict[str, str] = {
    "Hips": "Hips",
    "Spine02": "Spine1", "Spine01": "Spine2", "Spine": "Chest",
    "neck": "Neck1", "Head": "Head",
    "LeftShoulder": "LeftShoulder", "LeftArm": "LeftArm",
    "LeftForeArm": "LeftForeArm", "LeftHand": "LeftHand",
    "RightShoulder": "RightShoulder", "RightArm": "RightArm",
    "RightForeArm": "RightForeArm", "RightHand": "RightHand",
    "LeftUpLeg": "LeftLeg", "LeftLeg": "LeftShin",
    "LeftFoot": "LeftFoot", "LeftToeBase": "LeftToeBase",
    "RightUpLeg": "RightLeg", "RightLeg": "RightShin",
    "RightFoot": "RightFoot", "RightToeBase": "RightToeBase",
}

# BVH importer flags that reproduce kimodo's coordinate convention in Blender.
_BVH_IMPORT_KW = dict(global_scale=0.01, rotate_mode="NATIVE",
                      axis_forward="-Z", axis_up="Y",
                      update_scene_fps=False, update_scene_duration=False)


def import_soma_bvh(bvh_path: str, name: str = "SOMA_src"):
    """Import a kimodo SOMA BVH as a native SOMA armature; return the object.

    The rig's rest pose equals kimodo's neutral and the motion is already baked
    on by the importer (step 2). No retarget happens here.
    """
    import bpy

    before = {o.name for o in bpy.data.objects}
    bpy.ops.import_anim.bvh(filepath=bvh_path, **_BVH_IMPORT_KW)
    new = [o for o in bpy.data.objects if o.name not in before and o.type == "ARMATURE"]
    if not new:
        raise RuntimeError(f"BVH import produced no armature: {bvh_path}")
    soma = new[0]
    soma.name = name
    return soma


def _mapped_bones_parent_first(meshy_arm) -> list[str]:
    """Mapped Meshy bone names, every bone after its (mapped) ancestors."""
    bones = meshy_arm.data.bones
    ordered: list[str] = []
    seen: set[str] = set()

    def visit(b):
        if b.name in seen:
            return
        if b.parent is not None:
            visit(b.parent)
        seen.add(b.name)
        if b.name in MESHY_TO_SOMA:
            ordered.append(b.name)

    for b in bones:
        visit(b)
    return ordered


def retarget_soma_to_meshy(soma_arm, meshy_arm, action_name: str,
                           num_frames: Optional[int] = None,
                           frame_start: int = 1, apply_root: bool = False):
    """Copy SOMA rig world bone orientations onto the Meshy rig as a new action.

    Args:
        soma_arm:   source SOMA armature (from import_soma_bvh).
        meshy_arm:  target Meshy/Mixamo armature.
        action_name: name for the created Blender action.
        num_frames: frames to bake; defaults to the SOMA action's length.
        frame_start: first frame number (default 1).
        apply_root: also copy the SOMA Hips world-translation delta onto the
            Meshy Hips (locomotion). Off by default = walk in place.

    Returns the created bpy.types.Action.
    """
    import bpy
    from mathutils import Matrix

    if num_frames is None:
        src_action = (soma_arm.animation_data.action
                      if soma_arm.animation_data else None)
        num_frames = int(src_action.frame_range[1]) if src_action else 1

    order = _mapped_bones_parent_first(meshy_arm)
    if not order:
        raise RuntimeError("no mapped bones found on target rig "
                           f"'{meshy_arm.name}' (expected Mixamo names)")

    # Meshy armature-space rest orientations (constant); the object world rotation
    # cancels in the per-bone formula, so matrix_local (armature space) is correct.
    Rrest = {mb: meshy_arm.data.bones[mb].matrix_local.to_3x3() for mb in order}

    # nearest MAPPED ancestor for each mapped bone (chain may skip unmapped bones)
    parent_of: dict[str, Optional[str]] = {}
    for mb in order:
        p = meshy_arm.data.bones[mb].parent
        while p is not None and p.name not in MESHY_TO_SOMA:
            p = p.parent
        parent_of[mb] = p.name if p is not None else None

    if meshy_arm.animation_data is None:
        meshy_arm.animation_data_create()
    action = bpy.data.actions.new(name=action_name)
    action.use_fake_user = True
    meshy_arm.animation_data.action = action

    soma_world_rot = soma_arm.matrix_world.to_3x3()
    meshy_world_rot = meshy_arm.matrix_world.to_3x3()
    hips_src_name = MESHY_TO_SOMA.get("Hips")
    hips_ref = None  # source Hips world location at frame_start (for apply_root)

    for i in range(num_frames):
        f = frame_start + i
        bpy.context.scene.frame_set(f)

        # source world orientations for each mapped target bone
        S: dict[str, Matrix] = {}
        for mb, sn in MESHY_TO_SOMA.items():
            pb = soma_arm.pose.bones.get(sn)
            if pb is not None:
                S[mb] = (soma_world_rot @ pb.matrix.to_3x3()).to_quaternion().to_matrix()

        for mb in order:
            if mb not in S:
                continue
            pb = meshy_arm.pose.bones.get(mb)
            if pb is None:
                continue
            pb.rotation_mode = "QUATERNION"
            pn = parent_of[mb]
            if pn is not None and pn in S:
                basis = Rrest[mb].inverted() @ Rrest[pn] @ S[pn].inverted() @ S[mb]
            else:
                basis = Rrest[mb].inverted() @ S[mb]
            pb.rotation_quaternion = basis.to_quaternion()
            pb.keyframe_insert("rotation_quaternion", frame=f)

        if apply_root and hips_src_name and "Hips" in meshy_arm.pose.bones:
            sp = soma_arm.pose.bones.get(hips_src_name)
            mp = meshy_arm.pose.bones["Hips"]
            if sp is not None:
                world_pos = soma_arm.matrix_world @ sp.head
                if hips_ref is None:
                    hips_ref = world_pos.copy()
                delta = world_pos - hips_ref          # world-space travel
                # express delta in the Meshy Hips local frame
                mp.location = (meshy_arm.data.bones["Hips"].matrix_local.to_3x3().inverted()
                               @ meshy_world_rot.inverted() @ delta)
                mp.keyframe_insert("location", frame=f)

    action.frame_range = (frame_start, frame_start + num_frames - 1)
    print(f"[soma_retarget] {meshy_arm.name} <- {soma_arm.name}: "
          f"{num_frames} frames, {len(order)} bones (apply_root={apply_root})")
    return action


def apply_kimodo_bvh(bvh_path: str, meshy_arm, action_name: str = "KimodoMotion",
                     apply_root: bool = False, keep_source: bool = False):
    """Full step 2+3: import the SOMA BVH, retarget onto *meshy_arm*, clean up.

    Returns the created action. The temp SOMA rig is deleted unless keep_source.
    """
    import bpy

    soma = import_soma_bvh(bvh_path, name=f"{action_name}_SOMA_src")
    try:
        action = retarget_soma_to_meshy(soma, meshy_arm, action_name,
                                        apply_root=apply_root)
    finally:
        if not keep_source:
            data = soma.data
            bpy.data.objects.remove(soma, do_unlink=True)
            if data and data.users == 0:
                bpy.data.armatures.remove(data)
    return action
