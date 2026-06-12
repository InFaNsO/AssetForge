# Development Plan — AI Game-Asset Pipeline for Blender (v0.2)

**Companion to:** PROJECT_SPEC.md
**Updated:** 2026-06 — Meshy as primary AI backend + NVIDIA Kimodo generative animation

---

## 0. Architecture decision: Meshy-first pipeline

Meshy provides a complete REST API covering every AI stage of the game-asset pipeline.
Rather than wiring individual ML models for each stage, we use Meshy as the primary
AI backend and reserve Kimodo for generative (text-prompted) animation, which Meshy
does not offer (its animation is library-based, not generative).

```
INPUT (image / text / mesh)
      │
      ▼  Stage 3 — Generation
   Meshy Image/Text-to-3D  ◄── primary (paid)
   Copilot 3D              ◄── free fallback (manual GLB download)
      │
      ▼  Stage 4 — Retopology
   Meshy Remesh            ◄── primary (quad-dominant, trained on game meshes)
   Instant Meshes          ◄── local fallback (if exe path set in prefs)
   Decimate COLLAPSE       ◄── always-available fallback
      │
      ▼  Stage 5 — UV Unwrap
   Blender Smart UV        ◄── algorithmic (always)
      │
      ▼  Stage 6 — Baking
   Blender Cycles bake     ◄── algorithmic (normal/AO, optional)
      │
      ▼  Stage 7 — Texture
   Meshy Retexture         ◄── primary (text/image-guided PBR retexture)
   AssetForge enhance      ◄── fallback (delight → PBR decomp → upscale)
      │
      ▼  Stage 8 — Rigging
   Meshy Rigging           ◄── primary (humanoid only, outputs Mixamo-compatible rig)
   AssetForge auto-rig     ◄── fallback (bounding-box Rigify rig)
      │
      ▼  Stage 9 — Animation
   Meshy Animation library ◄── 584+ mocap clips (walk/run/fight/dance/idle...)
   Kimodo (NVIDIA)         ◄── generative: text prompt → novel motion (self-hosted)
      │
      ▼  Stages 10-13 — LOD / Collision / Export / Validate
   Blender algorithms      ◄── always (Decimate, convex hull, glTF export)
```

**Free path** (no API keys): Copilot 3D → Instant Meshes/Decimate → Blender UV/bake
→ AssetForge texture enhance → AssetForge auto-rig → no animation → Blender export.
Full quality path requires a Meshy key. Kimodo requires a local GPU (RTX 4080 works
with `TEXT_ENCODER_DEVICE=cpu`).

---

## 1. Phasing (updated)

| Phase | Goal | Status |
|-------|------|--------|
| 0 | Foundation: adapter/resolver/asset-state/stubs/CI | ✅ Done |
| 1 | Vertical slice: Copilot 3D + Tripo → full chain (stubs) | ✅ Done |
| 2 | Geometry algorithms: retopo/UV/bake/LOD/collision/export | ✅ Done |
| 3 | Generation breadth + Meshy Remesh | ✅ Done |
| 4 | Meshy Retexture (replaces algorithmic enhance as primary) | ✅ Done |
| 5 | Meshy Rigging (replaces Rigify as primary) | ✅ Done |
| 6 | Animation: Meshy library (584+ clips) + Kimodo generative | ✅ Done |
| 7 | MCP layer: stages as Claude tools | 🔜 Next |
| 8 | Full guided/expert stage-rail UI | 🔜 |
| 9 | Backend breadth: more adapters per demand | Ongoing |

---

## 2. Meshy API surface used

| Endpoint | Stage | Input | Output | Credits |
|---|---|---|---|---|
| `/openapi/v1/image-to-3d` | 3 | image URL / data URI | GLB + PBR maps | ~5 |
| `/openapi/v1/text-to-3d` | 3 | text prompt | GLB + PBR maps | ~5 |
| `/openapi/v1/remesh` | 4 | model URL / task ID | quad GLB | ~2 |
| `/openapi/v1/retexture` | 7 | model URL + style prompt | retextured GLB + PBR maps | ~10 |
| `/openapi/v1/rigging` | 8 | textured humanoid GLB | rigged FBX + GLB + walk/run | ~5 |
| `/openapi/v1/animations` | 9 | rig_task_id + action_id | animated FBX + GLB | ~2 |

All endpoints follow: POST to create (→ task_id), GET `/:id` to poll, result has URLs.

---

## 3. NVIDIA Kimodo (stage 9 — generative animation)

**What it is:** KInematic MOtion DiffusiOn — NVIDIA Research, released March 2026.
Text prompt → 3D skeletal body animation. 282 M params, trained on 700 h commercial mocap.
- Repo: https://github.com/nv-tlabs/kimodo (Apache 2.0, commercial use OK)
- Weights: NVIDIA Open Model License (commercial OK for SOMA skeleton)
- HuggingFace demo: https://huggingface.co/spaces/nvidia/Kimodo

**Hardware on this machine (RTX 4080 16 GB):**
Run with `TEXT_ENCODER_DEVICE=cpu` to offload the Llama-3 text encoder (16 GB) to
RAM (94 GB available). The motion model itself needs only ~3 GB VRAM. Feasible.

**Integration via Docker REST wrapper** (community, Apache 2.0):
```
docker run -p 9551:9551 -e HF_TOKEN=<token> \
    --gpus=all ghcr.io/eyalenav/kimodo-api:latest
POST http://localhost:9551/generate  {"prompt": "..."}  →  NPZ binary
```

**Output:** NPZ with SOMA joints (77 joints, SMPL-X-compatible first 24).
We convert: NPZ → Blender FCurves using joint index → Mixamo bone name mapping.

**Kimodo vs Meshy Animation:**

| Feature | Meshy Animation | Kimodo |
|---|---|---|
| Generative (text prompt) | ❌ (library only) | ✅ |
| Library clips | ✅ 584+ clips | ❌ |
| Cost | ~2 credits / clip | Free (self-hosted) |
| Hardware | Cloud | Local GPU needed |
| Output | FBX / GLB (ready to use) | NPZ (needs conversion) |
| Integration | REST API | Docker REST |

**Recommended usage:** use Meshy Animation for standard clips (idle, walk, run, fight),
use Kimodo for custom/specialized motion from text descriptions.

---

## 4. Backend connection strategy (updated §2.5)

| Stage | Primary | Fallback 1 | Fallback 2 |
|-------|---------|------------|------------|
| 3 Generation | Meshy image/text-to-3D | Tripo / Hunyuan (fal.ai) | Copilot 3D (free, manual) |
| 4 Retopology | Meshy Remesh | Instant Meshes | Decimate COLLAPSE |
| 5 UV | Blender Smart UV | — | — |
| 6 Bake | Blender Cycles | skip (no Cycles) | — |
| 7 Texture | Meshy Retexture | AssetForge enhance | passthrough |
| 8 Rigging | Meshy Rigging | AssetForge auto-rig | Rigify |
| 9 Animation | Meshy library + Kimodo | Mixamo (manual) | — |
| 10-13 | Blender algorithms | — | — |

---

## 5. Engineering practices (unchanged from v0.1 §3)

- **Stub-first.** Every stage works with a stub before a real backend lands.
- **Asset-state is the contract.** Stages read/write only the serializable asset object.
- **Provenance on every artifact.** Backend + params recorded (minus secrets).
- **Golden test mesh.** One mesh runs the whole chain in CI headless.
- **Validation gates between stages.** Cheap deterministic checks.

---

## 6. Remaining open decisions

- MCP server: build on existing connector vs custom (Phase 7) — module spec needed.
- Kimodo retargeting quality: SOMA→Meshy rig bone mapping needs live testing.
- Animation length stitching for Kimodo (long actions, loop transitions).
- Licensing review: all backends reviewed, Kimodo SOMA weights Apache 2.0 ✅.

---

*Phase 7 next deep-dive: MCP server spec — exposing stage operators as Claude tools.*
