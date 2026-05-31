"""Pipeline runner (DEVELOPMENT_PLAN.md §3).

Runs the stages in canonical order. For each applicable, non-skipped stage it asks the
resolver for a backend, runs it, records provenance, and runs a cheap validation gate.
In guided mode a failed gate stops the chain; in expert mode it warns and continues.

The chain must ALWAYS run end-to-end (stub-first). A stage with no backend registered is
treated as skippable-if-allowed, else it fails the gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from .adapter import BackendRegistry, RunContext, RunMode
from .asset_state import AssetState, StageStatus
from .provenance import ProvenanceEntry
from .resolver import resolve
from .stages import STAGES, stage as get_stage


class Mode(str, Enum):
    GUIDED = "guided"    # block on failed validation gate
    EXPERT = "expert"    # warn and continue


# A validator inspects state before/after a stage and returns (ok, message).
Validator = Callable[[AssetState, str], "ValidationResult"]


@dataclass
class ValidationResult:
    ok: bool
    message: str = ""


def always_ok(state: AssetState, stage_key: str) -> ValidationResult:
    return ValidationResult(True)


@dataclass
class StageResult:
    stage_key: str
    status: StageStatus
    backend: Optional[str] = None
    mode: Optional[str] = None
    reason: str = ""
    validation: Optional[ValidationResult] = None


@dataclass
class RunReport:
    results: list = field(default_factory=list)   # list[StageResult]

    @property
    def ok(self) -> bool:
        return all(r.status is not StageStatus.FAILED for r in self.results)

    def summary(self) -> str:
        lines = []
        for r in self.results:
            mark = {
                StageStatus.DONE: "OK ",
                StageStatus.SKIPPED: "-- ",
                StageStatus.NA: "n/a",
                StageStatus.FAILED: "XX ",
                StageStatus.MANUAL: "man",
            }.get(r.status, "?  ")
            extra = f" [{r.backend}:{r.mode}]" if r.backend else ""
            lines.append(f"  {mark} {r.stage_key:<10}{extra}  {r.reason}")
        return "\n".join(lines)


class Pipeline:
    def __init__(
        self,
        registry: BackendRegistry,
        validators: Optional[dict] = None,   # stage_key -> Validator
        mode: Mode = Mode.GUIDED,
    ) -> None:
        self.registry = registry
        self.validators = validators or {}
        self.mode = mode

    def _validate(self, state: AssetState, stage_key: str) -> ValidationResult:
        return self.validators.get(stage_key, always_ok)(state, stage_key)

    def run(
        self,
        state: AssetState,
        ctx: RunContext,
        skip: Optional[set] = None,
        params: Optional[dict] = None,
    ) -> RunReport:
        skip = skip or set()
        params = params or {}
        report = RunReport()

        for s in STAGES:
            current = state.status(s.key)

            if current is StageStatus.NA:
                report.results.append(StageResult(s.key, StageStatus.NA, reason="not applicable"))
                continue
            if s.key in skip or current is StageStatus.SKIPPED:
                state.set_status(s.key, StageStatus.SKIPPED)
                report.results.append(StageResult(s.key, StageStatus.SKIPPED, reason="skipped"))
                continue
            if current is StageStatus.MANUAL:
                report.results.append(StageResult(s.key, StageStatus.MANUAL, reason="done by hand"))
                continue

            res = resolve(s.key, self.registry, ctx, state)
            if not res.ok:
                # No backend: allowed to skip if the stage is skippable, else fail.
                if s.skippable:
                    state.set_status(s.key, StageStatus.SKIPPED)
                    report.results.append(
                        StageResult(s.key, StageStatus.SKIPPED, reason=res.reason))
                    continue
                state.set_status(s.key, StageStatus.FAILED)
                report.results.append(StageResult(s.key, StageStatus.FAILED, reason=res.reason))
                if self.mode is Mode.GUIDED:
                    break
                continue

            state.set_status(s.key, StageStatus.ACTIVE)
            stage_params = params.get(s.key, {})
            try:
                state = res.backend.run(res.mode, state, stage_params, ctx)
            except Exception as exc:  # a backend failure must not crash the chain
                state.set_status(s.key, StageStatus.FAILED)
                report.results.append(
                    StageResult(s.key, StageStatus.FAILED, res.backend.name,
                                res.mode.value, f"error: {exc}"))
                if self.mode is Mode.GUIDED:
                    break
                continue

            state.record(ProvenanceEntry.create(
                s.key, res.backend.name, res.mode.value, stage_params))

            vr = self._validate(state, s.key)
            if not vr.ok:
                state.set_status(s.key, StageStatus.FAILED)
                report.results.append(
                    StageResult(s.key, StageStatus.FAILED, res.backend.name,
                                res.mode.value, res.reason, vr))
                if self.mode is Mode.GUIDED:
                    break
                continue

            state.set_status(s.key, StageStatus.DONE)
            report.results.append(
                StageResult(s.key, StageStatus.DONE, res.backend.name,
                            res.mode.value, res.reason, vr))

        return report
