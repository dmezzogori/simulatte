---
version: 1
slug: "studio-apps-viewer-src-workspace-experiments-tsx"
primary_target: "studio/apps/viewer/src/workspace/experiments.tsx"
related_targets: ["studio/apps/viewer/src/workspace/comparison.tsx","studio/apps/viewer/src/workspace/analyses.tsx","studio/apps/viewer/src/workspace/shell.tsx","studio/apps/viewer/src/artifacts/view.tsx"]
---

# Studio results and analysis

Scope: Simulatte Studio experiment results (study Results), the cross-run comparison and the saved analysis page, plus the project sidebar reorganised around studies. Operate mode. This surface establishes the replacement visual world for all of Studio; DESIGN.md is written from it at finish.

Audience and job: a researcher beside their editor deciding which configuration differs on which KPI and how sure they can be; a consultant preparing client results; a student reading a first experiment. Success reverses QA18 ("everything is there and correct, it's just very hard to understand").

Confirmed decisions (2026-10-24): differences carry neutral sign only, no better/worse colour; a study is derived from an experiment definition (its runs, analyses whose inputs include them, presentations citing them), no new stored object; focused (2-8 configurations) and grid (30+) views are both first-class; colour encodes evidence state plus one reserved accent for the researcher's own layer; values print to the precision their interval supports, full digits on an explicit toggle; rows and columns never move under live updates; replications are countable marks; default baseline is the first configuration of the definition; cross-run comparison uses the same Results layout; hashes appear only inside provenance disclosures.

Out of scope here: run page and player, presentation editor and export, fixes QA3, QA9, QA14, QA33. They inherit the world later.

Known gap: experiment reports carry KPI keys but no units (units are declared on KPIs in Python but not persisted with results). Show units where the data carries them (analysis outputs); never invent one. Persisting KPI declarations is a backend follow-up. Likewise the comparison report carries no base seed, so the results foot states the seed rule (base seed + replication index, shared across configurations) instead of seed values; adding the base seed to `comparison.get` is a backend follow-up.

## Direction contract

THESIS: Every result is a row of a forest plot: an estimate with its interval on a shared axis, and every comparison is a difference drawn against a line of zero. Refuses the configuration-major database table with a toolbar on every output.

OWN-WORLD: White figure ground, ink #1B1D1F, hairline rules; a deep ink-blue rail (#13293D) carries navigation. Colour jobs: evidence blue (#0E5A9C) fills a difference whose interval excludes zero, hollow ink marks one that includes it; amber for partial or degenerate, red only for failures; violet (#6B3FA0) is reserved for the researcher's own layer (baseline, pinned findings). Source Serif 4 for page and KPI titles, Source Sans 3 with tabular figures for everything else. Squares for estimates, diamonds for differences.

STORY: The visitor opens a study, sees each KPI as a small figure, reads which configuration differs from the baseline and whether the interval clears zero, then traces any number to its replications, seeds and inputs.

FIRST VIEWPORT: Ink-blue rail with Studies (active study expanded: Results and Analyses; Replications is a section of Results, and Presentation waits for an index of which presentations cite a study) above Workspace. Work area: study title in serif, one status sentence (state, replications succeeded of planned, failures), one control line (level, Bonferroni, baseline, compare, start again). Below, KPI blocks in two columns at 1440: header with KPI name and unit, configuration rows (label, estimate text, square on axis, replication marks), difference rows with diamond against the zero line, one plain sentence per block. Convention and provenance line at the foot.

FORM: Forest Plot, IMPECCABLE'S PICK (first of my ordered list; the roll assigned Production Board, fifth). seed:439a3779386a3ed7. Signature interaction: choosing a baseline marks it in violet and redraws every block's difference rows around zero; grids beyond eight configurations focus one factor slice at full size, the other configurations kept as faint ticks on each axis, with a sortable matrix as the second view. Motion: interval marks extend from the estimate outward in 160 ms ease-out on update; nothing else animates; reduced motion shows the final state.

FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
