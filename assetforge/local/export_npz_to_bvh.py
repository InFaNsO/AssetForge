"""One-off: export a Kimodo NPZ to a native SOMA BVH using kimodo's own exporter.

This is a GROUND-TRUTH reference rig: kimodo's bvh exporter builds the SOMA77
skeleton at its own neutral rest pose and applies the model's local rotations
with its own rest-pose conversion (`from_standard_tpose`). No AssetForge retarget
is involved, so importing this BVH shows the motion exactly as kimodo intends it —
the control case for diagnosing the arm-abduction retarget bug.

Run in the kimodo conda env:
    conda run -n kimodo python assetforge/local/export_npz_to_bvh.py <in.npz> <out.bvh> [fps]
"""
import sys

import numpy as np
import torch

from kimodo.skeleton.registry import build_skeleton
from kimodo.exports.bvh import save_motion_bvh


def main() -> None:
    npz_path = sys.argv[1]
    out_path = sys.argv[2]
    fps = float(sys.argv[3]) if len(sys.argv) > 3 else 30.0

    d = np.load(npz_path, allow_pickle=True)
    lr = torch.tensor(np.asarray(d["local_rot_mats"]), dtype=torch.float32)
    rp = torch.tensor(np.asarray(d["root_positions"]), dtype=torch.float32)
    if lr.ndim == 5:
        lr = lr[0]
    if rp.ndim == 3:
        rp = rp[0]

    skel = build_skeleton(77)
    save_motion_bvh(out_path, lr, rp, skeleton=skel, fps=fps, standard_tpose=False)
    print(f"OK wrote {out_path}  frames={lr.shape[0]} joints={lr.shape[1]} fps={fps}")


if __name__ == "__main__":
    main()
