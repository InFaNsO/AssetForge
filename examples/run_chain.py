"""Runnable Phase-1 demo: drive the whole chain from plain Python (no Blender).

    py -3.12 examples/run_chain.py

Proves the vertical slice thread: one input -> every stage -> an 'exported' artifact,
using the stub registry. Swap in a real backend (with a key in .env) to see it for real.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from assetforge.core.adapter import RunContext
from assetforge.core.asset_state import AssetState, SourceKind
from assetforge.core.backends.stubs import build_stub_registry
from assetforge.core.pipeline import Mode, Pipeline
from assetforge.core.secrets import EnvSecretStore
from assetforge.core.stages import AssetType


def main() -> int:
    state = AssetState(
        id="demo",
        source_kind=SourceKind.IMAGE,
        source_ref="examples/input.png",
        asset_type=AssetType.HUMANOID,
    )
    ctx = RunContext(secrets=EnvSecretStore(), work_dir="work")
    report = Pipeline(build_stub_registry(), mode=Mode.GUIDED).run(state, ctx)

    print("Run report:")
    print(report.summary())
    print(f"\nResult: {'OK' if report.ok else 'FAILED'}")
    print(f"Exported artifact: {state.artifacts.get('exported')}")
    print(f"Provenance entries: {len(state.provenance)}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
