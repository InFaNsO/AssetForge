# Development Plan — AI Game-Asset Pipeline for Blender (v0.1)

**Companion to:** PROJECT_SPEC.md
**Scope:** how to actually build it — phasing, the backend-connection strategy, and the in-Blender menu/UX that walks a user through stages 1–13.

---

## 0. A note on "connect to all the options"

Connecting to *every* backend across 13 stages is dozens of integrations and is the surest way to never ship. This plan separates two things:

- **Connect to all *stages*** — yes. Every stage has an adapter slot from day one.
- **Connect to all *backends*** — no, not early. Wire **2 backends per critical stage** (one local, one API — satisfies the hybrid runtime), and ship a documented "write an adapter" path for the rest. The adapter interface is what makes "all the options" *possible* without making it *mandatory*.

Treat "all the options" as a capability of the architecture, not a Phase-1 deliverable.

---

## 1. Phasing overview

| Phase | Goal | Ships when |
|-------|------|-----------|
| 0 | Foundation: adapter interface, resolver, asset-state, stubs | Stubbed chain runs end-to-end |
| 1 | Vertical slice: ONE real backend through ALL stages | One test mesh → exported game-ready asset |
| 2 | Geometry algorithms hardened (retopo, LOD, collision, bake) | Deterministic stages production-quality |
| 3 | Generation stage: 2 backends (TRELLIS.2 local + 1 API) | Real input → mesh, backend-switchable |
| 4 | Texture enhancement: delight → PBR decomp → upscale → seams | Generator textures measurably improved |
| 5 | Rigging + canonical skeleton + 2 backends + Rigify fallback | Mesh → rigged on canonical skeleton |
| 6 | Animation: retargeter → Mixamo → 2 generative backends | Rig → animated, multi-source motion |
| 7 | MCP layer: stages exposed as tools | Claude can drive the full chain |
| 8 | UX polish: guided menu, batch, export presets, validation | The menu in §4 is complete |
| 9 | Backend breadth: add adapters per demand | Ongoing |

**Hard rule:** Phases 0 and 1 are non-negotiable and come first. They are the thin thread that proves the architecture. Everything after deepens a thread that already runs end-to-end.

---

## 2. The backend-connection strategy

### 2.1 Adapter contract

Every backend is an adapter implementing the §4.2 interface from the spec:

```
supports_local() / supports_api()
vram_required()
run_local(inputs, params) / run_api(inputs, params)
cost_estimate(inputs)   -> {time, credits, vram}
capabilities()          -> {stage, input_types, output_types, skeleton?, emits_quads?}
```

`capabilities()` is what lets the resolver and UI reason about a backend without hardcoding — e.g. "this generator emits quads, so offer to skip retopo."

### 2.2 Connection types (and the auth reality)

| Backend kind | Examples | Connection | Auth |
|--------------|----------|-----------|------|
| Local model | TRELLIS.2, UniRig, local diffusion | subprocess / local server, weights on disk | none |
| Vendor API | Tripo, Meshy, Rodin, DeepMotion | REST, adapter holds client | API key (user-supplied) |
| Aggregator API | Replicate, fal.ai | REST, many models via one key | one key, many models |
| Asset library | Mixamo | download + retarget, not inference | account/manual |
| MCP-exposed tool | blend-ai, Blender MCP | already in your connector set | per-connector |

**Key management:** API keys live in the addon preferences, never in the asset state or repo. One secrets store, read by adapters at call time. This is also a §10 risk if mishandled — keys in provenance logs would leak.

### 2.3 Resolver logic

Per stage, pick a backend by: explicit user choice → hardware probe (VRAM available?) → availability (key present? model downloaded?) → cost (time/credits). Always allow manual override in the UI. Resolver returns *why* it chose a backend (shown in UI for transparency).

### 2.4 Failure + fallback

- Local OOM → offer API fallback (don't silently switch; ask, since API costs credits).
- API auth fail → surface re-auth via the connector flow, don't crash the chain.
- Backend unavailable → stage is skippable or falls to algorithmic default where one exists (e.g. retopo → QuadriFlow).

### 2.5 Minimum backends per stage at each phase

| Stage | Phase 1 (slice) | Target (Phase 3–6) |
|-------|-----------------|--------------------|
| Generation | 1 (TRELLIS.2 or 1 API) | TRELLIS.2 + Hunyuan3D + 1 API |
| Retopo | QuadriFlow (algo) | + learned option |
| Texture enhance | passthrough stub | delight + PBR + upscale + seam |
| Rigging | Rigify (algo) | + UniRig + 1 API |
| Animation | Mixamo (1 clip) | + retarget + Kimodo + Hunyuan Motion + 1 video-mocap |
| LOD/Collision/Bake/Export | algo (final) | algo (final) |

---

## 3. Engineering practices (so phases don't rot)

- **Stub-first.** Every stage works with a stub before a real backend lands. The chain must always run.
- **Asset-state is the contract.** Stages read/write the serializable asset object only; no stage reaches into another's internals.
- **Provenance on every artifact.** Backend + params recorded (minus secrets).
- **Golden test mesh.** One mesh runs the whole chain in CI from Phase 1 on; regressions caught immediately.
- **Validation gates between stages.** Cheap deterministic checks; a stage can refuse bad input rather than propagate it.

---

## 4. The Blender menu / guided UX (stages 1–13)

The UI must do two contradictory things well: **guide a newcomer linearly** through 1–13, and **let an expert jump to any stage**. Design solves this with a persistent stage rail plus a contextual panel.

### 4.1 Layout

```
┌──────────────────────────────────────────────────────────┐
│  N-panel tab: "AssetForge"                                  │
├───────────────┬──────────────────────────────────────────┤
│  STAGE RAIL    │  ACTIVE STAGE PANEL                        │
│  (always       │  (changes with selected stage)            │
│   visible)     │                                           │
│                │  ┌─────────────────────────────────────┐ │
│  ✓ 1 Concept   │  │ Stage title + 1-line "what this does" │ │
│  ✓ 3 Generate  │  │                                       │ │
│  ▶ 4 Retopo    │  │ Backend: [resolver pick ▼] (why: ...)  │ │
│    5 UV        │  │ Params: ... (sane defaults)            │ │
│    6 Bake      │  │                                       │ │
│    7 Texture   │  │ [Run stage]   [Skip]   [Manual]        │ │
│    8 Rig       │  │                                       │ │
│    9 Animate   │  │ Validation: ⚠ unapplied scale          │ │
│    10 LOD      │  └─────────────────────────────────────┘ │
│    11 Collision│                                           │
│    12 Export   │  [< Prev stage]        [Next stage >]      │
│    13 Validate │                                           │
└───────────────┴──────────────────────────────────────────┘
```

### 4.2 Stage rail behavior

- Each stage shows state: **done ✓ / active ▶ / pending / skipped / N-A**.
- N-A is computed from asset state — e.g. a static prop greys out Rig/Animate; a quad-emitting generator marks Retopo "optional."
- Click any stage to jump (expert path). The rail never forces linearity, but **Next/Prev** buttons give newcomers a guided track.
- Hovering a stage shows the one-line "what goes into this step and why" — the pipeline education from our earlier discussion lives here as tooltips.

### 4.3 Active-stage panel — consistent anatomy per stage

Every stage panel has the same skeleton so the UI is learnable:

1. **Title + one-line purpose** (plain language).
2. **Backend selector** — resolver's pick pre-selected, dropdown to override, with a "why this was chosen" line (transparency from §2.3).
3. **Params** — collapsed to sane defaults; "Advanced" expander for the rest.
4. **Actions** — `Run` / `Skip` / `Do manually` (the last just opens the relevant Blender tools and marks the stage manual).
5. **Validation strip** — live cheap checks relevant to this stage, with one-click fixes where deterministic (Apply scale, Recalculate normals).
6. **Cost preview** — for API backends: estimated credits/time before running.

### 4.4 The "guide" layer (newcomer mode)

A toggle: **Guided** vs **Expert**.

- **Guided:** enforces Next/Prev flow, blocks advancing past a failed validation gate (with override), shows fuller explanations, defaults everything.
- **Expert:** rail free-navigation, terse panels, no gating, all params exposed.

This resolves the contradiction in §4 without two separate UIs — same panels, different guard-rails.

### 4.5 Top-level entry points

- **"New asset from image / text / mesh"** — sets input, jumps to the right starting stage.
- **"Run to end"** — runs all non-skipped stages with current backends (the closest thing to a one-button flow, but explicit about what it'll do and cost).
- **"Batch"** — folder in, runs the chain per item, report out.
- **MCP** (Phase 7+) — same operators, driven by Claude; the menu and MCP share the operator layer so they never diverge.

---

## 5. Risks specific to this plan

| Risk | Mitigation |
|------|-----------|
| "Connect to all backends" scope creep | §0 — adapters are a path, 2-per-stage is the deliverable |
| UI built before operators stable → rework | Menu is Phase 8; operators exist from Phase 1, UI is a thin shell over them |
| Guided/Expert modes diverging into two UIs | Same panels, mode only changes guard-rails (§4.4) |
| Resolver "why" being opaque → users distrust auto-pick | Always show reason + allow override (§2.3, §4.3) |
| Stages reaching into each other | Asset-state is the only contract (§3) |

---

## 6. Recommended immediate next steps

1. Build the **adapter interface + resolver + asset-state with stubs** (Phase 0). No models yet.
2. Define the **asset-state schema** concretely — it's the contract everything depends on.
3. Stand up the **golden test mesh + CI chain**.
4. Only then: wire the **first real backend** and run the Phase-1 slice.

The menu, however appealing to start with, comes after the operators it wraps exist. Building the panel first means rebuilding it.

---

*Next deep-dives available: asset-state schema · adapter/resolver detailed spec · texture-enhancement module · the stage-panel param sets.*
