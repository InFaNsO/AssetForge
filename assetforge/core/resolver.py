"""Backend resolver (DEVELOPMENT_PLAN.md §2.3).

Per stage, pick a backend + run mode by priority:
    1. explicit user choice
    2. hardware probe (does local fit in VRAM?)
    3. availability (key present? model present?)
    4. cost (prefer free/local, then cheapest credits, then fastest)

Always returns *why* it chose, so the UI can show it and offer an override.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .adapter import Backend, BackendRegistry, RunContext, RunMode
from .asset_state import AssetState


@dataclass
class Resolution:
    backend: Optional[Backend]
    mode: Optional[RunMode]
    reason: str

    @property
    def ok(self) -> bool:
        return self.backend is not None


def _mode_rank(mode: RunMode) -> int:
    # Prefer local (free, private) > automation (free but brittle) > api (costs credits).
    return {RunMode.LOCAL: 0, RunMode.AUTOMATION: 1, RunMode.API: 2}.get(mode, 3)


def resolve(
    stage_key: str,
    registry: BackendRegistry,
    ctx: RunContext,
    state: AssetState,
) -> Resolution:
    candidates = registry.for_stage(stage_key)
    if not candidates:
        return Resolution(None, None, "no backend registered for this stage")

    # 1. Explicit user choice wins if it is available in any mode.
    chosen_name = ctx.user_choice.get(stage_key)
    if chosen_name:
        chosen = next((b for b in candidates if b.name == chosen_name), None)
        if chosen is None:
            return Resolution(None, None, f"user chose {chosen_name!r} but it is not registered")
        avail = _first_available(chosen, ctx)
        if avail is not None:
            mode, _ = avail
            return Resolution(chosen, mode, f"user selected {chosen.name} ({mode.value})")
        # fall through: chosen unavailable, pick automatically and explain
        # (resolver still respects choice first, but won't dead-end the chain)

    # 2-4. Score every available (backend, mode) pair and take the best.
    scored: list[tuple[tuple, Backend, RunMode, str]] = []
    for b in candidates:
        for mode in b.supported_modes():
            available, why = b.is_available(ctx, mode)
            if not available:
                continue
            cost = b.cost_estimate(state, {})
            credits = cost.credits if cost.credits is not None else 0.0
            seconds = cost.seconds if cost.seconds is not None else 0.0
            # sort key: cheaper mode first, then fewer credits, then faster
            key = (_mode_rank(mode), credits, seconds)
            note = []
            if mode is RunMode.LOCAL:
                note.append("fits in VRAM" if b.vram_required() else "local")
            if credits:
                note.append(f"~{credits} credits")
            scored.append((key, b, mode, ", ".join(note) or mode.value))

    if not scored:
        return Resolution(None, None, "no available backend (missing keys / VRAM / models)")

    scored.sort(key=lambda t: t[0])
    _, best, best_mode, note = scored[0]
    reason = f"auto-picked {best.name} ({best_mode.value}; {note})"
    if chosen_name:
        reason = f"{chosen_name!r} unavailable -> " + reason
    return Resolution(best, best_mode, reason)


def _first_available(backend: Backend, ctx: RunContext):
    for mode in backend.supported_modes():
        ok, _ = backend.is_available(ctx, mode)
        if ok:
            return mode, _
    return None
