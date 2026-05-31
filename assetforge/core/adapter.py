"""Backend adapter interface — the keystone abstraction (PROJECT_SPEC.md §4.2,
DEVELOPMENT_PLAN.md §2.1).

Every ML/backed stage is an adapter. We extend the spec's two run modes to **three** so
that browser-driven backends with no API (e.g. Microsoft Copilot 3D for generation) are
first-class rather than a hack:

    LOCAL       weights on disk, subprocess / local server
    API         vendor / aggregator REST, key required
    AUTOMATION  browser-driven web app, no API (login + upload + download)

A backend declares which modes it supports and the resolver (resolver.py) chooses one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .asset_state import AssetState
from .secrets import SecretStore, get_api_key


class RunMode(str, Enum):
    LOCAL = "local"
    API = "api"
    AUTOMATION = "automation"
    ALGO = "algo"        # deterministic, no model (QuadriFlow, decimation, export...)
    STUB = "stub"        # placeholder used until a real backend lands


@dataclass(frozen=True)
class Capabilities:
    """What the resolver/UI can reason about without hardcoding (DEVELOPMENT_PLAN §2.1)."""

    stage: str
    input_types: tuple = ()      # e.g. ("image", "text")
    output_types: tuple = ()     # e.g. ("mesh",)
    skeleton: Optional[str] = None    # e.g. "mixamo" for rig/anim backends (§5)
    emits_quads: bool = False         # lets UI offer to skip retopo (§2.1)


@dataclass(frozen=True)
class CostEstimate:
    """Returned to the UI before running an API backend (DEVELOPMENT_PLAN §4.3)."""

    seconds: Optional[float] = None
    credits: Optional[float] = None   # API credits/$, None for free/local
    vram_mb: Optional[int] = None


@dataclass
class RunContext:
    """Everything a backend needs at call time that isn't asset state or params."""

    secrets: SecretStore
    work_dir: str = "."
    vram_free_mb: Optional[int] = None   # filled by the hardware probe (resolver.py)
    user_choice: dict = field(default_factory=dict)   # stage_key -> backend name


class Backend:
    """Base adapter. Subclass and implement the run mode(s) you support."""

    name: str = "backend"
    stage: str = ""              # a stages.Stage key
    secret_name: Optional[str] = None   # key name in the SecretStore, if API/automation

    # --- capability declaration ---
    def supports_local(self) -> bool:
        return False

    def supports_api(self) -> bool:
        return False

    def supports_automation(self) -> bool:
        return False

    def vram_required(self) -> Optional[int]:
        """MB of VRAM needed for the local path, or None if not applicable."""
        return None

    def capabilities(self) -> Capabilities:
        return Capabilities(stage=self.stage)

    def cost_estimate(self, state: AssetState, params: dict) -> CostEstimate:
        return CostEstimate()

    # --- availability (resolver uses this for the "availability" tier) ---
    def is_available(self, ctx: RunContext, mode: RunMode) -> tuple[bool, str]:
        """Return (available, human reason). Default: API/automation need a key."""
        if mode in (RunMode.API, RunMode.AUTOMATION) and self.secret_name:
            if not get_api_key(ctx.secrets, self.secret_name):
                return False, f"no key for {self.secret_name!r} in preferences"
        if mode == RunMode.LOCAL:
            need = self.vram_required()
            if need is not None and ctx.vram_free_mb is not None and ctx.vram_free_mb < need:
                return False, f"needs {need}MB VRAM, {ctx.vram_free_mb}MB free"
        return True, "available"

    def supported_modes(self) -> tuple[RunMode, ...]:
        modes = []
        if self.supports_local():
            modes.append(RunMode.LOCAL)
        if self.supports_api():
            modes.append(RunMode.API)
        if self.supports_automation():
            modes.append(RunMode.AUTOMATION)
        return tuple(modes)

    # --- execution: implement the modes you declared ---
    def run(self, mode: RunMode, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        fn = {
            RunMode.LOCAL: self.run_local,
            RunMode.API: self.run_api,
            RunMode.AUTOMATION: self.run_automation,
        }.get(mode)
        if fn is None:
            raise ValueError(f"{self.name}: unsupported mode {mode}")
        return fn(state, params, ctx)

    def run_local(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        raise NotImplementedError

    def run_api(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        raise NotImplementedError

    def run_automation(self, state: AssetState, params: dict, ctx: RunContext) -> AssetState:
        raise NotImplementedError


class BackendRegistry:
    """Holds the backends available for each stage (an adapter 'slot' per stage)."""

    def __init__(self) -> None:
        self._by_stage: dict[str, list] = {}

    def register(self, backend: Backend) -> Backend:
        self._by_stage.setdefault(backend.stage, []).append(backend)
        return backend

    def for_stage(self, stage_key: str) -> list:
        return list(self._by_stage.get(stage_key, []))

    def get(self, stage_key: str, name: str) -> Optional[Backend]:
        for b in self._by_stage.get(stage_key, []):
            if b.name == name:
                return b
        return None
