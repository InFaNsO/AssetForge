import bpy
import math
import numpy as np
import mathutils

# SOMA joint INDEX -> bone name, for each rig.
SOMA_IDX_TO_AZURE = {
    0:"Hips", 1:"Spine02", 2:"Spine01", 3:"Spine", 4:"neck", 6:"Head",
    11:"LeftShoulder", 12:"LeftArm", 13:"LeftForeArm", 14:"LeftHand",
    39:"RightShoulder", 40:"RightArm", 41:"RightForeArm", 42:"RightHand",
    67:"LeftUpLeg", 68:"LeftLeg", 69:"LeftFoot", 70:"LeftToeBase",
    72:"RightUpLeg", 73:"RightLeg", 74:"RightFoot", 75:"RightToeBase",
}
SOMA_IDX_TO_SOMA = {
    0:"Hips", 1:"Spine1", 2:"Spine2", 3:"Chest", 4:"Neck1", 6:"Head",
    11:"LeftShoulder", 12:"LeftArm", 13:"LeftForeArm", 14:"LeftHand",
    39:"RightShoulder", 40:"RightArm", 41:"RightForeArm", 42:"RightHand",
    67:"LeftLeg", 68:"LeftShin", 69:"LeftFoot", 70:"LeftToeBase",
    72:"RightLeg", 73:"RightShin", 74:"RightFoot", 75:"RightToeBase",
}


def fk_propagate(npz_path, target_rig_name, idx_to_bone, yup_fix=False, apply_root_loc=False):
    """Apply the raw NPZ local (parent-relative) rotations onto a rig via FK propagation.

    Recursive: traverse the TARGET skeleton from the Hips, passing the parent's world matrix
    down so each child accumulates from the root. The rest + child position matrices are the
    TARGET rig's own (point 2). Each joint's animation value is its NPZ parent-relative local
    rotation. No Y-up->Z-up correction (point 3). Written scale-safe via rotation_quaternion
    (point 4); Blender accumulates the world matrix down the hierarchy.
    """
    data = np.load(npz_path)
    local_rot = data["local_rot_mats"]
    if local_rot.ndim == 5:           # (1, F, 77, 3, 3) -> (F, 77, 3, 3)
        local_rot = local_rot[0]
    root_pos = data.get("root_positions")
    if root_pos is not None and root_pos.ndim == 3:
        root_pos = root_pos[0]
    F = local_rot.shape[0]

    tgt = bpy.data.objects[target_rig_name]
    for pb in tgt.pose.bones:
        pb.rotation_mode = 'QUATERNION'
    if not tgt.animation_data:
        tgt.animation_data_create()

    name_to_idx = {v: k for k, v in idx_to_bone.items()}
    mapped = set(idx_to_bone.values())

    # children/parent among mapped bones, walking the TARGET hierarchy
    def nearest_mapped_parent(b):
        p = b.parent
        while p:
            if p.name in mapped:
                return p.name
            p = p.parent
        return None
    children = {n: [] for n in mapped}
    parent = {}
    for n in mapped:
        b = tgt.data.bones.get(n)
        if not b:
            continue
        mp = nearest_mapped_parent(b)
        parent[n] = mp
        if mp:
            children[mp].append(n)
    root_names = [n for n in mapped if parent.get(n) is None]   # = Hips

    W = mathutils.Matrix.Rotation(math.radians(-90), 4, 'X') if yup_fix else mathutils.Matrix.Identity(4)
    scene = bpy.context.scene

    def npz_local_quat(fi, name):
        idx = name_to_idx[name]
        return mathutils.Matrix(local_rot[fi, idx].tolist()).to_4x4().to_quaternion()

    for fi in range(F):
        frame = scene.frame_start + fi
        scene.frame_set(frame)

        # recursive propagation from the root, passing the parent's world matrix down
        def propagate(name, parent_world):
            pb = tgt.pose.bones[name]
            local_q = npz_local_quat(fi, name)            # NPZ parent-relative local rotation
            pb.rotation_quaternion = local_q              # basis rotation (Blender propagates world)
            bpy.context.view_layer.update()
            world = parent_world @ pb.matrix              # this bone's accumulated world (object space)
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            for c in children[name]:
                propagate(c, world)

        for r in root_names:
            pb = tgt.pose.bones[r]
            pb.rotation_quaternion = npz_local_quat(fi, r)
            if apply_root_loc and root_pos is not None:
                rp = W @ mathutils.Vector(root_pos[fi].tolist())
                pb.location = tgt.matrix_world.inverted() @ rp
                pb.keyframe_insert(data_path="location", frame=frame)
            bpy.context.view_layer.update()
            pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
            for c in children[r]:
                propagate(c, tgt.matrix_world @ pb.matrix)

    print(f"FK propagate complete: {target_rig_name} ({F} frames, {len(mapped)} bones)")

# --- EXECUTE ---
