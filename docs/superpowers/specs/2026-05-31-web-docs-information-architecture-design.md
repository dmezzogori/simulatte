# Web Documentation Information Architecture — Design

- **Date:** 2026-05-31
- **Status:** Approved (brainstorm)
- **Tracking issue:** [#14 — Web docs improvement](https://github.com/dmezzogori/simulatte/issues/14)
- **Scope:** Item 5 (docs structure) as the *container* for items 1, 3, and 4. Item 2 (logo) is noted as independent and out of scope for this spec.

## Context

Issue #14 is an umbrella tracking issue for continuous improvement of Simulatte's web
documentation ([simulatte.dev](https://simulatte.dev), built with Zensical/MkDocs).
It lists five to-dos:

1. High-level component/architecture diagram (which classes exist, how they connect).
2. New logo with format variants.
3. More interesting tutorials.
4. Describe the API in further detail.
5. Gymnasium-inspired structuring of the web docs.

### Current state (as of v0.10.0)

- **Flat navigation** (`navigation.sections`): Home · Getting Started · API Reference ·
  Agent Skill · Tutorials · Intralogistics · Experimental.
- **API Reference (`reference.md`) covers only ~2 of 6 logical groups** — Release Policies
  and Dispatching Rules. Undocumented in the reference: the core classes (`Environment`,
  `ShopFloor`, `Server`, `ProductionJob`, `Router`, `PSP`, `Runner`), the utilities
  (`builders`, `distributions`, `logger`, `typing`), the **entire intralogistics** surface,
  and the **experimental** surface. This is the single largest gap.
- **No architecture/overview diagram** anywhere. Mermaid is already wired up (a custom fence
  in `zensical.toml`) and used in `intralogistics/examples.md`, so item 1 is low-friction.
- **Tutorials** are solid but mechanical ("here's how this building block works").
  `troubleshooting.md` is a 19-line stub. There is no end-to-end, scenario-driven tutorial.
- **Logo** is a single 6.9 MB PNG; no SVG, no light/dark variant, no favicon.

### Why structure first

We design the **information architecture first** (item 5) because it is the *container* that
determines where the diagram (item 1), the richer tutorials (item 3), and the expanded API
reference (item 4) live. With the skeleton fixed, those three become well-scoped follow-on
work that slots into named homes. The logo (item 2) is fully independent.

## Decision: Layer-first (Diátaxis) information architecture

Organize the **top level by documentation type** — the four Diátaxis quadrants
(Tutorial / How-to / Reference / Explanation) — rather than by subject/domain.
Production-planning and intralogistics are threaded through the shared sections, side by side.

### Rationale

- **Audience is "mostly one."** Users explore a single framework and frequently use both
  subsystems together. A layer-first structure avoids duplicating Tutorials / API / Concepts
  once per domain (which a domain-first structure would require).
- **Most faithful to Gymnasium's *transferable* structure.** Four of Gymnasium's five top
  tabs — Introduction, API, Tutorials, Development — are universal Diátaxis layers and map
  onto any framework. Only Gymnasium's **"Environments"** tab is domain-specific: it is a
  catalog of identically-shaped, instantiable artifacts. Simulatte has no such catalog; its
  truest analog is a **runnable Examples gallery**, *not* the two subsystems (which are
  narrative explanation, not browsable catalog entries).

### Diátaxis mapping (for reference)

| Quadrant | Reader's question | Simulatte home |
|---|---|---|
| Tutorial (learning) | "Teach me by building something." | Introduction · Tutorials |
| How-to (task) | "Show me the steps for a goal." | Tutorials · (Troubleshooting) |
| Reference (information) | "Give me the exact signature." | API Reference |
| Explanation (understanding) | "Help me understand how it fits & why." | Guides · Core Concepts & Architecture |

## Target navigation tree

```
1. Introduction
   - Overview                          (Diátaxis: learning)
   - Installation
   - Basic Usage
   - Core Concepts & Architecture      [NEW — item 1: high-level diagram + how classes connect]

2. Guides   (Explanation: "how each subsystem works & why")
   - Production Planning & Control      [NEW/expand]
   - Intralogistics                     [expand]
   - Reinforcement Learning (experimental) [expand]
   - Troubleshooting                    [from tutorials/troubleshooting.md; user-facing how-to]

3. Tutorials   (learning-oriented, hand-held)
   - Overview
   - Basic manufacturing system
   - Release control & dispatching
   - ShopFloor extensibility
   - Multi-run experiments
   - Logging
   - Gymnasium wrapper (RL)             [from experimental/gymnasium-wrapper.md]
   - + richer end-to-end walkthrough(s) [NEW — item 3]
   - + an intralogistics tutorial       [NEW — item 3]

4. Examples   (runnable-scenario gallery — the true "Environments" analog)
   - Overview
   - Production: draco_simple, focus_simple
   - Intralogistics: simple / intermediate / advanced
     (each entry: what it shows · layout diagram · code · annotated output)

5. API Reference   (grouped landing pages: short narrative intro + mkdocstrings autodoc)
   - Overview
   - Core              (Environment, ShopFloor, Server, ProductionJob, Router, PSP, Runner) [NEW — item 4]
   - Release Policies  (LumsCor, Slar, SlarLimit, Draco, ConWIP, ContinuousRelease, triggers, starvation_avoidance) [expand]
   - Dispatching Rules (Tier 1 / Tier 2 / Tier 3)
   - Intralogistics    (LayoutGraph, Warehouse, AGV, FleetCoordinator, Policies, Metrics, Traffic, Facilities, builders) [NEW — item 4]
   - Utilities         (builders, distributions, logger, typing)                            [NEW — item 4]
   - Experimental      (SimulatteEnv)                                                        [NEW — item 4]

6. Development
   - Contributing                       [NEW — surface CONTRIBUTING.md]
   - Agent Skill                        [from ai-skill.md]
   - Changelog                          [NEW — optional]
```

How the umbrella maps onto the skeleton:

- **Item 5** = the whole skeleton.
- **Item 1** = Introduction → *Core Concepts & Architecture*.
- **Item 3** = two homes: richer *Tutorials* + the *Examples* gallery.
- **Item 4** = a fully-fleshed *API Reference* (today only ~2 of 6 groups exist).
- **Item 2** = independent; touches Introduction/theme/favicon only.

## Page inventory & migration map

| Current file | Destination | Notes |
|---|---|---|
| `index.md` | Introduction → Overview | Landing page; keep logo/figure |
| (install section, duplicated) | Introduction → Installation | Consolidate the copy currently duplicated in `index.md` and `getting-started.md` |
| `getting-started.md` | Introduction → Basic Usage | |
| — | Introduction → Core Concepts & Architecture | **NEW** (item 1) |
| `intralogistics/index.md` | Guides → Intralogistics | Reframe as Explanation; keep import/concepts content |
| `experimental/index.md` | Guides → Reinforcement Learning | Expand conceptual intro |
| — | Guides → Production Planning & Control | **NEW/expand** conceptual overview |
| `tutorials/index.md` | Tutorials → Overview | |
| `tutorials/basic-manufacturing-system.md` | Tutorials | Keep |
| `tutorials/release-control-and-dispatching.md` | Tutorials | Keep |
| `tutorials/shopfloor-extensibility.md` | Tutorials | Keep |
| `tutorials/multi-run-experiments.md` | Tutorials | Keep |
| `tutorials/logging.md` | Tutorials | Keep |
| `experimental/gymnasium-wrapper.md` | Tutorials → Gymnasium wrapper (RL) | A how-to walkthrough; concept lives in Guides → RL |
| `tutorials/troubleshooting.md` | Guides → Troubleshooting | **Decision E** — a user-facing how-to, not a standalone tutorial |
| — | Tutorials → richer end-to-end + intralogistics tutorial | **NEW** (item 3) |
| `intralogistics/examples.md` | Examples → Intralogistics | Becomes the gallery's intralogistics entries |
| `examples/draco_simple.py`, `examples/focus_simple.py` | Examples → Production | **NEW** doc pages wrapping these scripts |
| `reference.md` (Release Policies block) | API Reference → Release Policies | Keep, expand to all policies |
| `reference.md` (Dispatching Rules block) | API Reference → Dispatching Rules | Keep |
| — | API Reference → Core / Intralogistics / Utilities / Experimental | **NEW** (item 4) |
| `ai-skill.md` | Development → Agent Skill | **Decision D** — move under Development |
| — | Development → Contributing | **NEW** — surface `CONTRIBUTING.md` |
| — | Development → Changelog | **NEW** — optional |

No existing content is dropped; every current page has a destination.

## Resolved design decisions

- **A. Examples is its own top-level section** (not folded into Tutorials). Separates
  "teach me a concept" (Tutorials) from "show me a whole working system" (Examples), and is
  the genuine Gymnasium "Environments" analog.
- **B. API Reference = grouped landing pages**, each a short narrative intro followed by
  mkdocstrings autodoc, expanded to cover all six groups. Directly satisfies item 4.
- **C. Keep a light "Guides" (Explanation) section.** Two substantial subsystems justify a
  dedicated conceptual home. (Could be folded into Introduction to be more Gymnasium-literal;
  we keep it explicit.)
- **D. Agent Skill moves under Development** (it is tooling/meta).
- **E. Troubleshooting** moves out of Tutorials into **Guides** as a user-facing how-to page
  rather than remaining a standalone tutorial stub.

## Scope

The implementation plan derived from this spec covers, **on a dedicated feature branch**, the
full structural move **and** the authored content for the new pages (not just empty stubs):

1. **Structural move** — new `nav` in `zensical.toml`, relocate/rename existing pages, and wire
   redirects for every moved URL.
2. **Stub creation *and* content** for every NEW entry in the tree:
   - **Item 1** — *Core Concepts & Architecture*: the high-level component diagram (mermaid)
     plus prose explaining how the classes connect.
   - **Item 3** — richer end-to-end tutorial(s) + an intralogistics tutorial; populated
     Examples gallery entries (what it shows · layout · code · annotated output) for the
     existing `draco_simple`, `focus_simple`, and the three intralogistics scripts.
   - **Item 4** — per-group API Reference pages for Core, Intralogistics, Utilities, and
     Experimental (each a narrative intro + mkdocstrings autodoc), plus an expanded Release
     Policies page.
   - Guides → *Production Planning & Control* concept page; Development → *Contributing*.

**Out of scope:** Item 2 (logo redesign + format variants) — independent, touches
theme/favicon only, handled in a separate effort.

### Workflow & phasing

All work is performed on a feature branch — `feature/web-docs-ia` off `main`, per
`CONTRIBUTING.md` (`feature/<name>`), merged via PR. **No direct commits to `main`.**

Because this authors a large amount of content, the plan is sequenced into phases that each
leave `zensical build` green and are independently reviewable:

- **Phase 0 — Branch.** Create `feature/web-docs-ia` off `main`.
- **Phase 1 — Skeleton.** New `nav`, relocate existing pages, redirects, and create the NEW
  pages as minimal stubs so the full tree builds green.
- **Phase 2 — API Reference (item 4).** Author the four new groups + expand Release Policies.
- **Phase 3 — Concepts & diagram (item 1).** Author *Core Concepts & Architecture* (with the
  mermaid component diagram) + the *Production Planning & Control* guide.
- **Phase 4 — Tutorials & Examples (item 3).** Author the new tutorial(s) and populate the
  Examples gallery.

Each phase boundary is a natural review/commit checkpoint on the branch.

## Implementation concerns (verify during implementation; not blocking the IA)

1. **Rendering — tabs vs. sections.** The "Gymnasium *look*" is top tabs. `zensical.toml`
   currently uses `navigation.sections`. zensical is a third-party tool; whether it supports
   `navigation.tabs` must be **verified against its feature set** before promising the tabbed
   rendering. The IA above is independent of tabs-vs-sections rendering.
2. **URL stability.** simulatte.dev is live. Moving/renaming pages changes URLs
   (e.g. `getting-started.md` → `introduction/basic-usage`, `intralogistics/index.md` →
   `guides/intralogistics`, `ai-skill.md` → `development/agent-skill`). Provide redirects
   (e.g. mkdocs-redirects or an equivalent zensical mechanism) for every moved page to avoid
   breaking inbound/external links.
3. **Publish hygiene.** Specs/plans live under `docs/superpowers/`, which MkDocs would publish
   as orphan pages (no build exclude is configured). Per commit `93b3c36`, prune
   `docs/superpowers/` from the published tree before release, **or** add a build-time exclude
   so planning docs never ship.
4. **Examples gallery balance.** Only two production examples exist (`draco_simple`,
   `focus_simple`) versus three intralogistics. Consider 1–2 additional production scenarios
   for balance, or accept the asymmetry initially.

## Success criteria

- All work lands on the `feature/web-docs-ia` branch and merges via PR — nothing committed
  directly to `main`.
- A single nav tree with six top sections: Introduction, Guides, Tutorials, Examples,
  API Reference, Development.
- Every existing page has a defined destination; no content is lost.
- API Reference covers all six logical groups (Core, Release Policies, Dispatching Rules,
  Intralogistics, Utilities, Experimental), each with a narrative intro + autodoc.
- Every NEW page in the tree ships with **real authored content**, not an empty stub: the
  architecture diagram renders, the four new API groups are populated, the new tutorials run,
  and every Examples gallery entry is filled in (item 2/logo excepted — out of scope).
- A decision on tabs-vs-sections rendering is made after verifying zensical support.
- Redirects exist for all moved pages.
- `zensical build` succeeds with no broken links at every phase boundary.
