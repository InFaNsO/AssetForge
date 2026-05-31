"""Instant Meshes retopology backend.

Instant Meshes (https://github.com/wjakob/instant-meshes) is a free, open-source
field-aligned quad remesher. It follows surface curvature to produce clean game-ready
quad topology — far better than any edge-collapse algorithm for character meshes.

Setup:
  1. Download the Windows binary from:
     https://instant-meshes.s3.eu-central-1.amazonaws.com/Release/instant-meshes-windows.zip
  2. Extract and copy InstantMeshes.exe anywhere on your machine.
  3. In Blender: Edit → Preferences → Add-ons → AssetForge → set "Instant Meshes path".

Pipeline:
  1. Export the active mesh as OBJ to a temp file (Blender → disk).
  2. Run Instant Meshes CLI as a subprocess.
  3. Import the result OBJ (disk → Blender).
  4. Swap the original object's mesh data for the remeshed data.

Instant Meshes does not preserve UVs — the UV stage (stage 5) re-unwraps, which is
correct anyway since the topology has changed.
"""
from __future__ import annotations

import os
import subprocess
import tempfile

import bpy

from assetforge.core.adapter import Backend, Capabilities, CostEstimate, RunContext, RunMode
from assetforge.core.asset_state import AssetState

from .utils import ensure_object, set_active

_WIN_DOWNLOAD = (
    "https://instant-meshes.s3.eu-central-1.amazonaws.com/Release/"
    "instant-meshes-windows.zip"
)


class InstantMeshesError(RuntimeError):
    pass


class InstantMeshesBackend(Backend):
    name = "instant_meshes"
    stage = "retopo"

    def supports_local(self) -> bool:
        return True

    def capabilities(self) -> Capabilities:
        return Capabilities("retopo", input_types=("mesh",), output_types=("mesh",),
                            emits_quads=True)

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate(seconds=30.0, credits=None)  # free, local

    def is_available(self, ctx: RunContext, mode: RunMode):
        path = _get_exe_path()
        if not path:
            return False, (
                "Instant Meshes not configured. "
                f"Download from {_WIN_DOWNLOAD}, "
                "then set the path in Edit → Preferences → Add-ons → AssetForge."
            )
        if not os.path.exists(path):
            return False, f"Instant Meshes executable not found at: {path}"
        return True, f"Instant Meshes found: {path}"

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        exe = _get_exe_path()
        if not exe or not os.path.exists(exe):
            raise InstantMeshesError("Instant Meshes executable not found")

        obj = ensure_object(state)
        if obj is None:
            raise InstantMeshesError("No mesh object in scene")

        target_faces   = int(params.get("target_faces", 15_000))
        smooth_iters   = int(params.get("smooth_iterations", 2))
        crease_angle   = float(params.get("crease_angle", 30.0))
        deterministic  = bool(params.get("deterministic", True))

        faces_before = len(obj.data.polygons)
        print(f"[AssetForge] Instant Meshes: {faces_before} → target {target_faces}")

        with tempfile.TemporaryDirectory() as tmp:
            in_obj  = os.path.join(tmp, "input.obj")
            out_obj = os.path.join(tmp, "output.obj")

            _export_obj(obj, in_obj)
            _run_instant_meshes(exe, in_obj, out_obj,
                                target_faces, smooth_iters, crease_angle, deterministic)
            _import_and_swap(obj, out_obj)

        faces_after = len(obj.data.polygons)
        print(f"[AssetForge] Instant Meshes done: {faces_before} → {faces_after} polys")

        state.artifacts["topology"] = "quad"
        state.artifacts["blender_object"] = obj.name
        state.metadata.setdefault("retopo", {}).update(
            {"method": "instant_meshes",
             "faces_before": faces_before,
             "faces_after": faces_after})
        return state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_exe_path() -> str:
    """Read the Instant Meshes executable path from addon preferences."""
    prefs = bpy.context.preferences.addons.get("assetforge")
    if prefs is None:
        return ""
    return getattr(prefs.preferences, "instant_meshes_path", "") or ""


def _export_obj(obj, filepath: str) -> None:
    """Export *obj* as OBJ. Uses wm.obj_export (Blender 3.3+)."""
    set_active(obj)
    try:
        bpy.ops.wm.obj_export(
            filepath=filepath,
            export_selected_objects=True,
            export_uv=False,
            export_normals=True,
            export_materials=False,
            export_triangulated_mesh=False,
        )
    except Exception as exc:
        raise InstantMeshesError(f"OBJ export failed: {exc}") from exc
    if not os.path.exists(filepath):
        raise InstantMeshesError("OBJ export produced no file")


def _run_instant_meshes(exe: str, in_obj: str, out_obj: str,
                         faces: int, smooth: int, crease: float,
                         deterministic: bool) -> None:
    """Call the Instant Meshes CLI as a subprocess."""
    cmd = [
        exe,
        in_obj,
        "--output",  out_obj,
        "--faces",   str(faces),
        "--rosy",    "4",    # 4-RoSy field → quad-dominant output
        "--posy",    "4",    # 4-PoSy positioning
        "--smooth",  str(smooth),
        "--crease",  str(int(crease)),
    ]
    if deterministic:
        cmd.append("--deterministic")

    print(f"[AssetForge] running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        raise InstantMeshesError("Instant Meshes timed out (>3 min)") from exc
    except FileNotFoundError as exc:
        raise InstantMeshesError(f"Could not launch Instant Meshes: {exc}") from exc

    if result.returncode != 0:
        raise InstantMeshesError(
            f"Instant Meshes failed (exit {result.returncode}):\n"
            f"{result.stderr or result.stdout}"
        )
    if not os.path.exists(out_obj):
        raise InstantMeshesError("Instant Meshes ran but produced no output file")


def _import_and_swap(original_obj, obj_path: str) -> None:
    """Import the remeshed OBJ and swap its mesh data into *original_obj*."""
    bpy.ops.object.select_all(action="DESELECT")
    try:
        bpy.ops.wm.obj_import(filepath=obj_path)
    except Exception as exc:
        raise InstantMeshesError(f"OBJ import failed: {exc}") from exc

    imported = next(
        (o for o in bpy.context.selected_objects if o.type == "MESH"), None)
    if imported is None:
        raise InstantMeshesError("OBJ import produced no mesh object")

    # Swap mesh data: replace original mesh with remeshed one, keep object identity.
    old_mesh = original_obj.data
    new_mesh = imported.data.copy()
    new_mesh.name = old_mesh.name
    original_obj.data = new_mesh

    # Clean up: remove the temporary imported object and the old mesh.
    bpy.data.objects.remove(imported, do_unlink=True)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)

    set_active(original_obj)
