# AI Game-Asset Pipeline for Blender — Project Specification (v0.1)

**Status:** Draft / top-level architecture
**Scope of this document:** the whole system at a high level. Per-module deep-dives (generation, retopology, rigging, animation, texturing-enhancement) are separate documents.

---

## 1. What this is

A Blender addon plus MCP layer that takes an asset from an input (image, text, or existing mesh) through to a **game-ready** export, by orchestrating AI models and deterministic algorithms across the standard game-asset pipeline.

It is **not** a single "generate a finished asset" button. It is a set of independent, toggleable stages a user can enter and exit at any point — because AI-generated assets and hand-authored assets need different subsets of the pipeline.

### Design principles

1. **Pluggable backends.** No model is hardwired. Each ML stage is an interface with multiple implementations (local + API). A model being abandoned should cost one adapter, not a rewrite.
2. **ML for generation/taste, algorithms for geometry/measurement.** If the output has a provably correct answer (decimation, baking, collision, export transforms), use an algorithm. If it requires hallucinating missing detail or taste (sculpt, texture, motion), use ML.
3. **Stages are independent and reorderable.** Quad-emitting generators can skip retopo; static props skip rigging; etc.
4. **Enhance, don't regenerate.** For texturing, the v1 path improves generator output rather than re-deriving it from scratch (see §6).
5. **Human handoff is a feature.** Every stage outputs something a human can pick up and refine. The plugin never traps the asset in an all-or-nothing flow.
6. **Validation is first-class, not an afterthought.** Cheap deterministic checks (scale, normals, manifold, UV overlap) run between stages and at export.

---

## 2. Runtime model (decided)

**Hybrid: local where possible, API fallback.**

- Local inference for users with 24GB+ VRAM (TRELLIS.2, UniRig, local diffusion for texture enhancement).
- API fallback (Tripo / Meshy / Rodin / Replicate-hosted) for weaker machines or when a local model is unavailable.
- The backend interface (§4) abstracts this: a stage requests a capability, the resolver picks local or API per the user's config and available hardware.

**Implication:** every ML stage adapter must implement both a local and an API path, or explicitly declare one unsupported. VRAM probing and graceful fallback are part of the core, not per-module.

---

## 3. The pipeline stages

Mapped to the 13-step game-asset pipeline. Tag legend: **ML** / **ALGO** / **HYBRID** / **MANUAL** (human, plugin assists only).

| # | Stage | Type | Plugin role |
|---|-------|------|-------------|
| 1 | Concept / reference | ML | Optional image-gen for refs (upstream aid) |
| 2 | Blockout | MANUAL/ALGO | Out of scope v1 (procedural primitives only) |
| 3 | Generation (image/text → mesh) | ML | Core. Pluggable backends |
| 4 | Retopology | HYBRID | Quad remesh (algo) + learned option; skippable for quad-emitting backends |
| 5 | UV unwrap | HYBRID | Algo unwrap/pack + ML seam suggestion |
| 6 | Baking | ALGO | Normal/AO/curvature bake, high→low |
| 7 | Texture enhancement | ML | Core. Delight + PBR decomp + upscale + seam repair (see §6) |
| 8 | Rigging | ML+ALGO | UniRig / API; Rigify fallback; standardized skeleton |
| 9 | Animation | ML+ALGO | Generative motion + retarget; Mixamo lib option |
| 10 | LODs | ALGO | Quadric decimation chain |
| 11 | Collision | ALGO | Convex hull / V-HACD decomposition |
| 12 | Export | ALGO | Multi-engine presets (user choice) |
| 13 | Validation | ALGO(+ML) | Inter-stage + pre-export checks |

**v1 core focus:** stages 3, 4, 7, 8, 9, 10, 11, 12, 13. Stage 7 (texture enhancement) and stage 3 (generation) carry the most ML risk; stage 7's multi-view-free enhancement approach is the deliberate risk reduction.

---

## 4. Architecture

### 4.1 Three layers

```
┌─────────────────────────────────────────────┐
│  MCP layer (conversational orchestration)     │  ← Claude drives the chain,
│  exposes stages as MCP tools                  │     handles fuzzy glue work
├─────────────────────────────────────────────┤
│  Blender addon (operators + UI panel)         │  ← native operators per stage,
│  each stage = a callable operator             │     sane defaults, manual entry
├─────────────────────────────────────────────┤
│  Backend interface (capability resolver)      │  ← local vs API per stage,
│  adapters: TRELLIS.2, Tripo, UniRig, ...      │     VRAM probe + fallback
└─────────────────────────────────────────────┘
```

**Why both addon and MCP:** the addon makes each stage a deterministic, callable operator with defaults (good for batch, scripting, reproducibility). The MCP layer lets Claude orchestrate the whole chain conversationally and handle the judgment-heavy glue — seam decisions, weight cleanup, retarget fixes — that is painful to hardcode. They are not redundant: addon = the verbs, MCP = the director.

### 4.2 Backend interface (the keystone)

Every ML stage implements a common shape:

```
Backend(capability):
    supports_local() -> bool
    supports_api()   -> bool
    vram_required()  -> int | None
    run_local(inputs, params) -> outputs
    run_api(inputs, params)   -> outputs
    cost_estimate(inputs)     -> {time, credits, vram}
```

A **resolver** picks the implementation per stage from: user preference → hardware probe → availability → cost. This is the single most important abstraction in the project. Build and test it with stubbed backends before wiring real models.

### 4.3 Asset state object

A single serializable object travels the pipeline, carrying mesh, UVs, texture maps, skeleton, animations, metadata, and a **provenance log** (which backend/params produced each artifact). This enables: resuming mid-pipeline, reproducibility, batch processing, and validation knowing what to check.

---

## 5. The standardized skeleton (critical cross-stage decision)

The hardest integration problem is **skeleton mismatch** between rigging output and generative motion. Decided up front, not last:

- **Adopt a Mixamo / SMPL-X-compatible humanoid skeleton as the canonical target** for v1 humanoids.
- Auto-rig (UniRig/API) targets it; generative motion (Kimodo/Hunyuan Motion) retargets onto it; Mixamo library works natively.
- Non-humanoid (quadruped, mechanical) is a **post-v1** concern — generative human-motion libraries mostly don't apply anyway.

Without this, stages 8 and 9 silently produce garbage. The retargeter (algorithmic) maps any source skeleton → canonical via a hand-maintained bone mapping table.

---

## 6. Texturing approach (decided: enhancement-first)

v1 **enhances generator output** rather than re-deriving textures from scratch. Rationale: the generator already solved mesh-awareness (placing plausible, geometry-following detail); the remaining defects are mostly *enhancement* problems that reuse the same ML components a from-scratch pipeline needs, while skipping the riskiest stage (multi-view generation + projection + blending).

```
Generator output (base color w/ baked light, partial PBR, on UVs)
   → [Delight]            ML   strip baked lighting   (biggest single win)
   → [PBR decomposition]  ML   derive roughness/metallic/height
   → [Upscale / detail]   ML   super-res low texel-density regions
   → [Seam repair]        ALGO + ML inpainting
   → Refined PBR set
```

**Known ceiling:** enhancement can't rescue an asset the generator failed entirely, and hero close-ups still hand off to manual Substance work. From-scratch multi-view generation is kept as an **optional later backend**, not v1.

---

## 7. Cross-cutting features (make game-dev work easier)

- **Batch processing** — folder of inputs → game-ready assets unattended; compounding time savings for asset libraries.
- **Engine export presets** — Unity / Unreal / Godot / engine-agnostic glTF, with correct scale, axis, smoothing, pivot. User chooses per export.
- **Pre-flight validation** — analyze scene before/after each stage: unapplied scale, missing UVs, non-manifold geometry, flipped normals, UV overlap, triangle/draw-call budget. Cheap, prevents the most common export failures.
- **LOD chain generation** — algorithmic decimation to N levels with budget targets.
- **Provenance / reproducibility** — every artifact records its source backend + params.

---

## 8. Build order (proposed)

1. **Backend interface + resolver, with stubbed backends.** Prove local/API switching and the asset state object before any real model. *Highest-leverage, lowest-glamour — do not skip.*
2. **Vertical slice with ONE real backend through the WHOLE chain** on a single test mesh: generate → retopo → UV → bake → (placeholder texture) → rig → one animation → LOD → collision → export → validate. A thin end-to-end thread beats any polished single stage.
3. **Generation stage** — TRELLIS.2 local + one API backend.
4. **Geometry algorithms** — retopo, decimation/LOD, collision, baking (mostly proven libraries; low risk).
5. **Texture enhancement** — delight first (biggest win), then PBR decomp, upscale, seam repair.
6. **Rigging** — canonical skeleton + UniRig/API + Rigify fallback.
7. **Animation** — retargeter first (algorithmic), then generative motion + Mixamo.
8. **MCP layer** — expose stages as tools once operators are stable.
9. **Batch + export presets + validation polish.**

**Anti-goal:** building four polished connectors that don't meet in the middle. The retargeting/skeleton seam and the backend resolver are where projects like this fail — build those first, prove the thread, then deepen.

---

## 9. Open decisions (to resolve in module specs)

- Specific delight / PBR-decomposition / upscale models (local + API) — texturing module spec.
- Retopo: when to auto-skip (quad backends) vs. force — retopo module spec.
- Animation length stitching (Kimodo 10s cap) — animation module spec.
- MCP server: build on existing (blend-ai / official Claude–Blender connector) vs. custom — MCP module spec.
- Licensing review per backend for commercial use — separate compliance pass.

---

## 10. Known risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| Skeleton mismatch (stages 8↔9) | High | Canonical skeleton decided up front (§5) |
| Backend resolver complexity | High | Build first with stubs (§8.1) |
| Texture enhancement ceiling | Medium | Enhancement-first, manual handoff, optional from-scratch later |
| Running TRELLIS.2 + others on one consumer GPU | Medium | Hybrid runtime; API fallback; sequential not parallel |
| Vendor API churn / model abandonment | Medium | Pluggable backends; ≥2 backends per critical stage |
| Generative motion quality vs. Mixamo | Low | Mixamo library as reliable default |

---

*Next: pick a module to deep-dive. Recommended order matches §8 — backend interface, then the vertical slice, then generation or texturing.*
