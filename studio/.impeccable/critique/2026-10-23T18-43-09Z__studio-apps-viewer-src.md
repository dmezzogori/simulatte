---
target: the whole Studio web UI
total_score: 23
max_score: 40
na_heuristics: 
p0_count: 0
p1_count: 4
target_identity: "file:/Users/davide/Developer/simulatte/.worktrees/studio-project-experience/studio/apps/viewer/src"
timestamp: 2026-10-23T18-43-09Z
slug: studio-apps-viewer-src
---
Method: dual-agent (A: design review subagent · B: detector + CDP browser subagent). Both shared one authenticated headless tab in turn (Studio's session is per-tab); overlay injection was blocked by Studio's CSP, so no user-visible overlay.

## Design Health Score

| # | Heuristic | Score | Key Issue |
|---|---|---|---|
| 1 | Visibility of System Status | 2 | Busy-slot notice at page bottom, never cleared when the slot frees (models.tsx:618-634, :243, :305); overview "20 saved runs" vs sidebar "Runs 22"; running experiments show no elapsed time or slot view |
| 2 | Match System / Real World | 2 | "descriptive convention", "replication_t", int64/float64 under headers, "Registration key", output keys like matplotlib_png and lateness_distribution |
| 3 | User Control and Freedom | 3 | Escape and Cancel work; Delete sits beside the primary action; compare route silently drops bad ids |
| 4 | Consistency and Standards | 2 | Two different player transports; export dialog on legacy styling (style.css:338-346); experiments open via buttons, other lists via links |
| 5 | Error Prevention | 3 | Previews before register/export, refusals with remedies; but KPI-only runs offered for presentations |
| 6 | Recognition Rather Than Recall | 2 | Identity by hash (Run cbe4e061, snapshot d4b5c4bc2eb2, 20-hash "Original run" select); config-major comparison rows |
| 7 | Flexibility and Efficiency | 2 | Runs not sortable, no multi-select compare, three controls plus Apply to sort, no shortcut hints |
| 8 | Aesthetic and Minimalist Design | 2 | Saved analysis is 5569px for seven 1-2 row tables with 14 toolbars; run page packs 5 actions, transport and 6 filters into a 570px box |
| 9 | Error Recovery | 3 | Classified failures with hints; wrong "Renderer not recorded" message leaks into Present mode (QA33) |
| 10 | Help and Documentation | 2 | Many inline hints, no links to docs, no explanation of half-width, Bonferroni, degenerate, convention |
| **Total** | | **23/40** | **Acceptable (low end)** |

## Design Specificity Verdict

LLM assessment: category-interchangeable admin shell (sidebar, breadcrumb, eyebrow + H1, count tiles, white cards, uppercase 10px headers, pill badges). The product's identity (replication-level statistics, traceable evidence) appears only as text in the same generic vocabulary: intervals are "± 10.7037" cells next to an "ok" badge, findings carry 15-digit floats, evidence for a client is a snake_case card with a hash. No visual encoding of uncertainty, no "finding" primitive, no study-level object linking experiment, analyses and presentation. "Numbers before pictures" is inverted on the run page.

Deterministic scan: CLI 1 finding (overused-font: Inter, style.css:2); the stylesheet-driven ws-* markup gives the static scan little to match. Live CDP scans on 8 routes: about 480 findings, dominated by undersized-ui-text (10px headers, badges, eyebrows), low-contrast (#667a80 at 4.2-4.4:1 on hints and headers; small.af-dtype at 2.7:1, 44 hits on the saved analysis; sidebar counts 3.3-3.7:1), tiny-text 11px qualifiers in presentation evidence, nested-cards on analysis and evidence cards, skipped headings on run and experiment pages. Agrees with the design review's WCAG findings; kicker-above-heading and stragglers are low-signal.

## Priority Issues

1. [P1] Results rendered as database tables, not findings (root of QA18). Every table output, even 1-row summaries, gets the full paging/sort/filter toolbar (artifacts/table.tsx:86-130); raw floats; intervals split across MEAN/LOWER/UPPER columns. Fix: a finding primitive (value, unit, precision policy, inline interval, n, plain-language convention note); order by meaning; table controls only when rows exceed a page. Commands: shape, distill, typeset, clarify.
2. [P1] The core comparison is not laid out as a comparison. Config-major rows (compare row 6 with row 14), uncertainty as text, "ok" even when the half-width exceeds the mean at n=3, baseline buried in a form. Fix: KPI-major matrix with configurations as columns, inline interval marks showing overlap, difference-vs-baseline column, evidence-strength state; baseline chosen first. Commands: shape, layout.
3. [P1] Run page treats the recording as the result (QA7 and QA9 share one root). Fixed 570px player box; scalars in a collapsed details with raw floats; KPI-only runs show "No recording available" and no outputs (run.get omits scalars). Fix: KPI summary first on every run; player as a full-viewport inspection mode; Present/Export secondary. Commands: shape, layout, harden.
4. [P1] Identity by hash. Hex prefixes in H1s, evidence and pickers; "Unassigned" for the recordings presentations depend on. Fix: generated human names (model, configuration, seed, date), hashes diagnostic-only. Commands: clarify, shape.
5. [P2] Presentation authoring and export too small for their job (QA30, QA37). 14-control right column; evidence at 30% width with 10-11px qualifiers and diagnostics; export preview of a 1920x1080 canvas inside a 700px modal. Fix: typed evidence blocks with a reader-facing caption grammar, diagnostics in presenter notes, export preview as a page-level stage. Commands: shape, layout.

Cross-cutting (feeds the redesign's type and colour system): pervasive 10-11px text and muted #667a80 just under 4.5:1 fail the WCAG 2.2 AA floor in PRODUCT.md.

## Persona Red Flags

Alex (power user): experiment rows are buttons, no middle-click; runs not sortable, no multi-select or compare-these-two; sorting needs three controls plus Apply; no shortcut hints except Escape.
Sam (WCAG 2.2 AA): 10px headers/badges/eyebrows, 4.2-4.4:1 hints, 2.7:1 dtype labels, 3.7:1 sidebar counts; fixed 570px player breaks at 200% zoom; skipped heading levels; busy-slot alert at the end of the DOM.
Thesis student (first run): "Registration key", "Entrypoint · module path and factory"; no "create your first experiment" path; KPI-only success reads as failure; "descriptive convention" and "degenerate" unexplained.
Industrial client (watching a presentation): sees lateness_distribution, 15-digit quantiles, "partial snapshot · descriptive", a snapshot hash, "Renderer not recorded"; negative lateness without sign convention; chart without title, axis or unit; no opening context.

## Minor Observations

Overview tile "Project environment · Ready" links to experiments; experiment names styled as chips; "what the controls send" and "protect from pruning" developer phrasing; seconds-precision timestamps on every row; run notes column always occupies space; H2 15px vs body 14px; Inter flagged as overused; aborted /api/prefix request on route change (likely benign).

## Questions to Consider

- If the unit of research is a finding (KPI + interval + provenance), should the IA be Models → Studies (experiment + analyses + presentation together) rather than six parallel lists?
- Should the experiment page itself be the KPI-by-configuration interval view with differences against a baseline, tables below as evidence?
- Should the player be a mode (full viewport, like Present) rather than a box inside a page that leads with numbers?
- Who is ever meant to read a hash, and who names runs, recordings and snapshots?
- For a client, is a presentation an animation with evidence beside it, or a sequence of evidence with the animation as one item?
