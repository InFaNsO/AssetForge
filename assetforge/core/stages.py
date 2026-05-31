"""The 13 pipeline stages (PROJECT_SPEC.md §3).

A stage is metadata only: an id, ordering, plain-language purpose, its type tag, and the
rule for when it is Not-Applicable to a given asset. Backends attach to a stage by its
:class:`Stage` ``key``. Keeping this declarative lets the UI (Phase 8) and the resolver
reason about stages without hardcoding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class StageType(str, Enum):
    """How a stage is implemented (PROJECT_SPEC.md §3 tag legend)."""

    ML = "ML"           # generation / taste: hallucinate detail
    ALGO = "ALGO"       # provably-correct geometry / measurement
    HYBRID = "HYBRID"   # algo with an optional learned component
    MANUAL = "MANUAL"   # human does it; plugin only assists


class AssetType(str, Enum):
    """Drives which stages are Not-Applicable. Inferred or user-set."""

    UNKNOWN = "unknown"
    STATIC = "static"        # prop: no rig, no animation
    HUMANOID = "humanoid"    # full rig + animation on canonical skeleton (§5)
    QUADRUPED = "quadruped"  # rig yes, generative humanoid motion N/A (post-v1)
    MECHANICAL = "mechanical"


@dataclass(frozen=True)
class Stage:
    """A declarative pipeline step."""

    number: int          # 1..13, the canonical pipeline position
    key: str             # stable slug used by backends + asset state
    name: str
    type: StageType
    purpose: str         # one-line, plain language (shown as UI tooltip in Phase 8)
    # Asset types this stage does NOT apply to (greyed out / marked N-A in the rail).
    na_for: frozenset = field(default_factory=frozenset)
    # If True the stage can be skipped without breaking downstream stages.
    skippable: bool = False

    def applies_to(self, asset_type: AssetType) -> bool:
        return asset_type not in self.na_for


# Ordered canonical pipeline. v1 core focus = stages 3,4,7,8,9,10,11,12,13 (spec §3).
STAGES: tuple[Stage, ...] = (
    Stage(1, "concept", "Concept / reference", StageType.ML,
          "Optional reference-image generation to feed the pipeline.",
          skippable=True),
    Stage(2, "blockout", "Blockout", StageType.MANUAL,
          "Rough primitive blockout. Out of scope in v1 (procedural primitives only).",
          skippable=True),
    Stage(3, "generate", "Generation", StageType.ML,
          "Turn an image or text prompt into a base mesh."),
    Stage(4, "retopo", "Retopology", StageType.HYBRID,
          "Rebuild messy generated geometry into clean topology.",
          skippable=True),
    Stage(5, "uv", "UV unwrap", StageType.HYBRID,
          "Flatten the mesh to 2D so textures can be applied."),
    Stage(6, "bake", "Baking", StageType.ALGO,
          "Bake high-res detail (normal / AO / curvature) onto the low-res mesh."),
    Stage(7, "texture", "Texture enhancement", StageType.ML,
          "Improve generator textures: delight, PBR decomposition, upscale, seam repair."),
    Stage(8, "rig", "Rigging", StageType.HYBRID,
          "Add a skeleton so the mesh can be posed and animated.",
          na_for=frozenset({AssetType.STATIC, AssetType.MECHANICAL})),
    Stage(9, "animate", "Animation", StageType.HYBRID,
          "Add motion to the rigged mesh.",
          na_for=frozenset({AssetType.STATIC, AssetType.MECHANICAL}),
          skippable=True),
    Stage(10, "lod", "LODs", StageType.ALGO,
          "Generate lower-detail versions for distant rendering."),
    Stage(11, "collision", "Collision", StageType.ALGO,
          "Build collision shapes the game engine can use cheaply."),
    Stage(12, "export", "Export", StageType.ALGO,
          "Write the asset out with correct scale/axis for the target engine."),
    Stage(13, "validate", "Validation", StageType.ALGO,
          "Final pre-export checks: manifold, normals, UV overlap, budgets."),
)

# Lookups
STAGE_BY_KEY: dict[str, Stage] = {s.key: s for s in STAGES}
STAGE_BY_NUMBER: dict[int, Stage] = {s.number: s for s in STAGES}


def stage(key: str) -> Stage:
    """Look up a stage by key, raising a clear error if unknown."""
    try:
        return STAGE_BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown stage key {key!r}; known: {sorted(STAGE_BY_KEY)}"
        ) from None
