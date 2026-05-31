"""Provenance log: record which backend + params produced each artifact.

Secrets must NEVER land here (PROJECT_SPEC.md §10). :func:`strip_secrets` is applied to
every params dict before it is recorded.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

_SECRET_HINTS = ("key", "secret", "token", "password", "auth", "credential")


def strip_secrets(params: dict) -> dict:
    """Return a copy of ``params`` with any secret-looking values redacted."""
    clean: dict = {}
    for name, value in params.items():
        if any(hint in name.lower() for hint in _SECRET_HINTS):
            clean[name] = "<redacted>"
        elif isinstance(value, dict):
            clean[name] = strip_secrets(value)
        else:
            clean[name] = value
    return clean


@dataclass
class ProvenanceEntry:
    stage: str
    backend: str
    mode: str                       # local / api / automation / algo / stub
    params: dict = field(default_factory=dict)
    timestamp: str = ""

    @classmethod
    def create(cls, stage: str, backend: str, mode: str, params: dict | None = None):
        return cls(
            stage=stage,
            backend=backend,
            mode=mode,
            params=strip_secrets(params or {}),
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "backend": self.backend,
            "mode": self.mode,
            "params": self.params,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ProvenanceEntry":
        return cls(
            stage=d["stage"], backend=d["backend"], mode=d["mode"],
            params=d.get("params", {}), timestamp=d.get("timestamp", ""),
        )
