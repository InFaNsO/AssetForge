"""The asset-state object — the single serializable contract that travels the pipeline
(PROJECT_SPEC.md §4.3). Stages read/write ONLY this object; no stage reaches into
another's internals (DEVELOPMENT_PLAN.md §3).

Artifacts are stored as references (file paths / opaque handles), never live ``bpy`` data,
so the contract stays Blender-independent and JSON-serializable.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum

from .provenance import ProvenanceEntry
from .stages import STAGES, AssetType


class StageStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"
    SKIPPED = "skipped"
    NA = "na"          # not applicable to this asset type
    FAILED = "failed"
    MANUAL = "manual"  # user did it by hand; plugin assisted


class SourceKind(str, Enum):
    IMAGE = "image"
    TEXT = "text"
    MESH = "mesh"


@dataclass
class AssetState:
    """Everything known about one asset as it moves through the pipeline."""

    id: str
    source_kind: SourceKind = SourceKind.IMAGE
    source_ref: str = ""                  # image path, prompt text, or mesh path
    asset_type: AssetType = AssetType.UNKNOWN

    # Artifact references produced by stages. Opaque to core; stages agree on keys.
    # e.g. {"mesh": "work/abc/mesh.glb", "uv": True, "textures": {...}, "skeleton": ...}
    artifacts: dict = field(default_factory=dict)

    # stage key -> StageStatus
    stage_status: dict = field(default_factory=dict)

    provenance: list = field(default_factory=list)   # list[ProvenanceEntry]
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Initialise status for any stage not explicitly set: NA where the stage does
        # not apply to this asset type, else PENDING.
        for s in STAGES:
            if s.key not in self.stage_status:
                self.stage_status[s.key] = (
                    StageStatus.PENDING if s.applies_to(self.asset_type)
                    else StageStatus.NA
                )

    # --- status helpers ---
    def set_status(self, stage_key: str, status: StageStatus) -> None:
        self.stage_status[stage_key] = status

    def status(self, stage_key: str) -> StageStatus:
        return self.stage_status[stage_key]

    def record(self, entry: ProvenanceEntry) -> None:
        self.provenance.append(entry)

    # --- serialization (the contract must round-trip) ---
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_kind": self.source_kind.value,
            "source_ref": self.source_ref,
            "asset_type": self.asset_type.value,
            "artifacts": self.artifacts,
            "stage_status": {k: v.value for k, v in self.stage_status.items()},
            "provenance": [p.to_dict() for p in self.provenance],
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AssetState":
        obj = cls(
            id=d["id"],
            source_kind=SourceKind(d.get("source_kind", "image")),
            source_ref=d.get("source_ref", ""),
            asset_type=AssetType(d.get("asset_type", "unknown")),
            artifacts=dict(d.get("artifacts", {})),
            stage_status={k: StageStatus(v) for k, v in d.get("stage_status", {}).items()},
            provenance=[ProvenanceEntry.from_dict(p) for p in d.get("provenance", [])],
            metadata=dict(d.get("metadata", {})),
        )
        return obj

    def to_json(self, **kw) -> str:
        return json.dumps(self.to_dict(), indent=2, **kw)

    @classmethod
    def from_json(cls, text: str) -> "AssetState":
        return cls.from_dict(json.loads(text))
