# Auto Asset Development

> **AI-powered game-asset pipeline for Blender** — take an input (image, text, or existing mesh) through the full 13-stage game-art pipeline to a game-ready export, with pluggable AI and algorithmic backends at every step.

![License](https://img.shields.io/badge/license-MIT-green)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![Blender](https://img.shields.io/badge/blender-4.1%2B-orange)

---

## What this is

A **Blender addon + (later) MCP layer** that walks an asset from raw input to a game-engine-ready export by orchestrating AI models and deterministic algorithms across the standard game-art pipeline:

```
Image / Text / Mesh
       ↓
  1. Concept       optional reference generation
  2. Blockout      rough primitive (manual / procedural)
  3. Generation    image/text → base mesh  ← AI (Copilot 3D free / Tripo API)
  4. Retopology    clean quad topology     ← AI + algo (QuadriFlow)
  5. UV Unwrap     flatten to 2D           ← algo + AI seam suggestion
  6. Baking        normal/AO/curvature     ← algo
  7. Texture       delight → PBR → upscale → seam repair  ← AI
  8. Rigging       skeleton + weights      ← AI (UniRig / Rigify fallback)
  9. Animation     generative motion       ← AI + Mixamo library
 10. LODs          quadric decimation chain ← algo
 11. Collision     convex hull / V-HACD    ← algo
 12. Export        Unity / Unreal / Godot / glTF ← algo
 13. Validation    pre-export checks       ← algo
       ↓
  Game-ready asset
```

**It is not a one-button magic tool.** Every stage is independent and skippable — because AI-generated assets and hand-authored assets need different subsets of the pipeline. You enter and exit wherever you are in the workflow.

---

## Key design principles

| Principle | What it means |
|-----------|--------------|
| **Pluggable backends** | No model is hardwired. Each ML stage is an interface with multiple implementations (local, API, browser-automation). Replacing a model costs one adapter, not a rewrite. |
| **ML for generation/taste, algorithms for geometry** | If the output has a correct answer (decimation, baking, collision), use an algorithm. If it requires missing detail or artistic judgement, use ML. |
| **Stages are independent** | Quad-emitting generators skip retopo. Static props skip rigging. You run exactly the stages you need. |
| **Free first, paid fallback** | Generation defaults to **Microsoft Copilot 3D** (free, no account required beyond Microsoft login, outputs GLB). A paid API (Tripo/Meshy/Rodin) is available as the robust fallback. |
| **Human handoff is a feature** | Every stage outputs something you can pick up and refine manually. The addon never traps your asset. |
| **Secrets never leave your machine** | API keys live in Blender's addon preferences, never in your `.blend` files, exported assets, or this repo. |

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  MCP layer (Phase 7+)                        │  Claude drives the full chain
│  stages exposed as MCP tools                 │  handles fuzzy glue work
├─────────────────────────────────────────────┤
│  Blender addon  (operators + N-panel)        │  native operators per stage
│  assetforge/blender_addon/                   │  sane defaults, manual entry
├─────────────────────────────────────────────┤
│  Core engine  (pure Python, NO bpy)          │  local vs API per stage
│  assetforge/core/                            │  VRAM probe + fallback
└─────────────────────────────────────────────┘
```

The core engine (`assetforge/core`) **never imports `bpy`**, so the entire 13-stage chain can be tested headlessly in CI without Blender or a GPU. The Blender layer is a thin shell over it.

---

## Requirements

| Component | Minimum | Notes |
|-----------|---------|-------|
| **OS** | Windows 10 / Linux | macOS untested |
| **Blender** | 4.1 | Bundles Python 3.11 — all addon code targets 3.11 |
| **Python** | 3.11+ | For running tests and the CLI demo outside Blender |
| **GPU** | Any NVIDIA | 8 GB+ VRAM for future local texture/rig ML stages |
| **TRELLIS.2 local** | ❌ | Needs 24 GB VRAM + Linux — out of scope; use Copilot 3D or a paid API |
| **Copilot 3D** | Free | Microsoft account; image → GLB; no API |
| **Tripo / Meshy / Rodin** | Optional API key | Paid; deterministic; scriptable; CI-testable |

---

## Repository layout

```
assetforge/
  core/                 Pure Python — runs headless in CI
    stages.py             13 stage definitions + AssetType rules
    asset_state.py        Serializable pipeline contract (JSON round-trips)
    adapter.py            Backend interface (local / api / automation)
    resolver.py           Picks backend: choice → VRAM → key → cost; returns *why*
    secrets.py            get_api_key() — env for dev, AddonPreferences in Blender
    provenance.py         Records backend+params; strips secrets before logging
    pipeline.py           Chain runner with guided/expert validation gates
    backends/
      stubs.py            Stub per stage (the chain always runs, even with no models)
      registry.py         Assembles the real registry (Phase 1+)
      generation/
        copilot3d.py      Free browser-automation backend
        drivers.py        BrowserDriver protocol: Manual / Playwright / MCP (Phase 7)
        tripo.py          Tripo REST API adapter (stdlib urllib, zero extra deps)
  blender_addon/        bpy layer — thin shell over core
    __init__.py           Module wiring + register/unregister
    prefs.py              AddonPreferences: API key fields (subtype=PASSWORD)
    operators.py          Run to End / Reset operators
    panel.py              Minimal N-panel (full stage-rail UI is Phase 8)
examples/
  run_chain.py          CLI demo: stub mode / Copilot 3D / Tripo
tests/
  golden/               Golden test mesh (cube.obj) and placeholder GLB
  test_asset_state.py   Schema + JSON round-trip + secret-stripping
  test_resolver.py      Backend selection logic
  test_pipeline.py      Full 13-stage golden-mesh end-to-end run
  test_generation.py    Copilot 3D + Tripo adapters + vertical slice
.github/workflows/
  ci.yml                Runs the core test suite on Python 3.11 (no Blender / GPU)
```

---

## Getting started

### 1. Clone and run the tests (no Blender needed)

```powershell
git clone https://github.com/InFaNsO/Auto-Asset-Development.git
cd "Auto-Asset-Development"
python -m unittest discover -s tests -v
# Expected: Ran 24 tests ... OK
```

### 2. Run the CLI demo (no Blender needed)

```powershell
# Stub mode — all 13 stages run instantly with placeholder backends
python examples/run_chain.py

# Real free generation — download a GLB from Copilot 3D first, then:
python examples/run_chain.py --image photo.png --glb downloaded_model.glb

# Paid API generation (needs a key in .env)
# Copy .env.example to .env and fill in ASSETFORGE_TRIPO_API_KEY
python examples/run_chain.py --image photo.png --tripo
```

### 3. Install the Blender addon

```powershell
# Creates a junction so Blender sees the addon live from the repo (no re-install on changes)
$addons = "$env:APPDATA\Blender Foundation\Blender\4.1\scripts\addons"
New-Item -ItemType Directory -Force -Path $addons | Out-Null
New-Item -ItemType Junction -Path "$addons\assetforge" -Target "$PWD\assetforge"
```

Then in Blender: **Edit ▸ Preferences ▸ Add-ons** → search **AssetForge** → enable it.

### 4. Use it in Blender

1. In the 3D viewport press **N**, select the **AssetForge** tab.
2. Set **Asset type** (Static / Humanoid / etc.).
3. In **Copilot 3D GLB**, browse to a GLB you downloaded from [Copilot 3D](https://copilot.microsoft.com/labs/experiments/copilot-3d).
4. Click **Run to End** — the model is imported into your scene and all applicable stages are run.

To use a paid API key, enter it in **Edit ▸ Preferences ▸ Add-ons ▸ AssetForge** (stored locally, never in the repo).

---

## Free generation with Copilot 3D

1. Go to **https://copilot.microsoft.com/labs/experiments/copilot-3d** (sign in with a Microsoft account — free).
2. Upload an image with a **single clear subject** on a clean background.
3. Click **Create**, wait ~60 seconds, download the **GLB**.
4. Feed the GLB into AssetForge via the CLI (`--glb`) or the addon's file picker.

> Copilot 3D quality is consumer-grade. For production work or batch pipelines, a paid API (Tripo/Meshy/Rodin) is more reliable and scriptable.

---

## API keys

Keys are entered in **Blender's addon preferences** and never written to your `.blend`, exported assets, or this repository. For CLI usage, copy `.env.example` to `.env` and fill in your key — `.env` is git-ignored.

```powershell
copy .env.example .env
# Edit .env: set ASSETFORGE_TRIPO_API_KEY=your_key_here
```

---

## Development roadmap

| Phase | Focus | Status |
|-------|-------|--------|
| 0 | Foundation: adapter interface, resolver, asset-state, stubs, CI | ✅ Done |
| 1 | Vertical slice: Copilot 3D + Tripo through all stages | ✅ Done |
| 2 | Geometry algorithms hardened: retopo, LOD, collision, bake | 🔜 Next |
| 3 | Generation breadth: Hunyuan3D + additional APIs | Planned |
| 4 | Texture enhancement: delight → PBR → upscale → seam repair | Planned |
| 5 | Rigging: canonical skeleton + UniRig + Rigify fallback | Planned |
| 6 | Animation: retargeter + Mixamo + generative motion | Planned |
| 7 | MCP layer: stages exposed as Claude tools | Planned |
| 8 | Full guided/expert stage-rail UI | Planned |
| 9 | Backend breadth: more adapters per demand | Ongoing |

---

## Troubleshooting

### Imported rig "explodes" into a spiky star of giant bones

**Symptom:** after importing a Meshy/Mixamo rig, the armature draws as a starburst of
enormous octahedral bones radiating from the character — sometimes tens of metres long —
hiding the mesh. Animation still plays correctly; only the bone *display* is wrong.

**Cause:** the glTF/GLB format stores **no bone tail or length** — a bone is just a joint
node (a head position plus an orientation). On import, Blender's glTF importer has to
*guess* each bone's tail. For some Meshy rigs that guess produces tails pointing in the
correct direction but at a wildly wrong distance (observed: up to 4881 local units ≈ 48 m
at the 0.01 cm-export scale). Because the octahedral display draws each bone head→tail,
every bone becomes a giant spike. The *direction* is right, which is why the rest
orientation (`matrix_local`) — and therefore skinning and any baked animation — is
unaffected; only the length is wrong.

**Fix (automatic):** `fix_armature_bone_display()` in
`assetforge/blender_addon/backends/geometry/utils.py` clamps each over-long tail back to
its nearest child joint **along its existing direction**, leaving head, direction and roll
byte-for-byte identical. It is idempotent (a no-op on healthy rigs) and runs on every
import path: `import_mesh`, `ensure_object`, the addon import operator, and — as a
whole-scene safety net — `import_animation_glb`, so a rig that arrived spiky from a manual
import is healed by the next animation import.

**If you imported a rig outside AssetForge** (raw `bpy.ops.import_scene.gltf`, drag-and-drop)
and it is spiky now, just import any animation onto it via AssetForge, or call
`fix_armature_bone_display()` directly — both repair the whole scene in place.

---

## Contributing

1. Fork the repo and create a branch.
2. Run `python -m unittest discover -s tests` — all 24 tests must stay green.
3. Add a test for any new backend or stage behaviour.
4. Open a pull request with a clear description of what changed and why.

The core rule: `assetforge/core` must **never** import `bpy`. That invariant is what keeps CI fast and lets the engine run outside Blender.

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, sell things made with it. Attribution appreciated but not required.
