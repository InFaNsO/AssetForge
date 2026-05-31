# AssetForge — AI Game-Asset Pipeline for Blender

A Blender addon + (later) MCP layer that walks an asset from input (image / text / mesh)
through the 13-stage game-art pipeline to a game-ready export, orchestrating pluggable
AI/algorithm backends behind one adapter interface.

See [`PROJECT_SPEC.md`](PROJECT_SPEC.md) (the *what*) and
[`DEVELOPMENT_PLAN.md`](DEVELOPMENT_PLAN.md) (the *how*).

## Repository layout

```
assetforge/
  core/            Pure-Python pipeline engine (NO bpy import — runs in CI without Blender)
    asset_state.py   The serializable contract that travels the pipeline
    stages.py        The 13 stage definitions
    adapter.py       Backend interface (local / api / automation) + capabilities + cost
    resolver.py      Picks a backend per stage (choice -> hardware -> availability -> cost)
    secrets.py       get_api_key() abstraction (env for dev, AddonPreferences in Blender)
    provenance.py    Provenance log (records backend + params, strips secrets)
    pipeline.py      Runs the chain with validation gates
    backends/
      stubs.py       Stub backend for every stage (the chain always runs)
  blender_addon/   The bpy layer (operators + preferences + minimal panel)
tests/             unittest suite incl. the golden-mesh end-to-end stub chain
.github/workflows/ CI: runs the core suite on Python 3.11
```

**Design rule:** `assetforge/core` must never `import bpy`. Blender-only code lives in
`assetforge/blender_addon`. This is what lets the full chain be tested headlessly.

## Current status — Phase 0 (Foundation)

- [x] Asset-state schema (the contract)
- [x] Adapter interface + resolver, stubbed backends
- [x] Golden test mesh + CI chain (all 13 stages run end-to-end as stubs)

## Running the tests

```powershell
# from repo root, any Python 3.11+ (no Blender, no GPU needed)
py -3.12 -m unittest discover -s tests -v
```

## API keys

Keys are entered in the **Blender addon preferences** and read by each adapter at call
time. They are never written to the asset state, the provenance log, an exported asset,
or this repo. For local development, adapters also read `os.environ` (see `.env.example`).
GitHub Secrets are used only by CI, and only for a single disposable test key if a live
API test is ever enabled.
