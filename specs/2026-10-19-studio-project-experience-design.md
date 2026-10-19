# Studio: project experience, analysis and presentation

- **Status:** draft for Davide's written review. The product decisions in §2 were agreed in conversation; the implementation choices in the following sections are proposals for this review. This document does not claim implementation or acceptance evidence.
- **Baseline:** released Simulatte 0.17.0 and local `main` at `d3e17e6` (PR #67), inspected on eva. Recheck the implementation baseline before development.
- **Purpose:** turn the released Studio capabilities into a coherent project experience, with clear CLI entry, numerical analysis, usable workflows and presentation quality.
- **Predecessors:** [global Studio design](2026-10-08-studio-global-design.md), [SP4](2026-10-11-sp4-studio-design.md), [SP5](2026-10-12-sp5-experiments-design.md). Their original rollout is complete; this is subsequent product development.
- **Visual reference:** [interactive workspace mockup](mockups/2026-10-19-studio-project-workspace.html). Open the HTML directly in a browser. Values and execution are illustrative. Its focused navigation is selected; detailed appearance is a working direction.
- **Execution location:** finalize the design and implementation plan on eva; develop and run heavier checks on bigboss.

## 1. Product brief

Simulatte provides production-control and logistics abstractions built on SimPy. Researchers must remain able to work entirely in Python, inspecting numerical results, logs and plots. Studio gives the same models a persistent workspace for execution, investigation, experiments, analysis and presentation.

The project is the main unit users open. It contains related model entrypoints, their inputs, study definitions and saved results. Model behavior stays in Python. Studio supports both a first successful run and sustained professional use without requiring users to understand internal execution requests, transport protocols or storage schemas.

The three supported uses are daily research, onboarding and client presentation. The design does not select one audience at the expense of the others. Numerical analysis must be useful without creating any plots. Presentations must be usable directly in Studio and exportable.

## 2. Decisions agreed with Davide

| Area | Agreed behavior |
| --- | --- |
| Project | Multiple named model entrypoints in one persistent project. |
| Entry command | `simulatte studio` opens the current folder. `simulatte studio <folder>` opens the specified folder and creates it if absent. `.` remains valid. |
| Onboarding | Open prepared projects immediately; guide existing Python projects through setup; offer a runnable starter in an empty project. Configuration remains readable outside Studio. |
| Environments | uv-managed project environments by default; an existing Python environment remains an advanced option. Preparation is visible, dependency updates explicit. |
| Execution | Explicit Run by default; optional automatic reruns during model development. Experiments always require an explicit start. |
| Recording | Individual runs default to full recording. Experiments default to numerical results without animation traces. |
| Retention | Save runs, outputs and provenance; support names, annotations and marking important results. Protect outputs referenced by saved analyses or presentations. |
| Analysis | Built-ins and custom Python metrics, calculations and plots are essential. Named populations can use attributes or predicates. |
| Iteration | Populations and measurements are usually declared before experiments. Reanalyze retained data when sufficient; otherwise run revised experiments. |
| Delivery | Direct Studio presentation first, video second, standalone HTML third; all remain available. |
| Navigation | Focused pages and a persistent project sidebar. The proposed extra work-tab bar duplicated navigation and was rejected. |
| Plot support | Native numerical outputs; Matplotlib, including Seaborn through Matplotlib; Plotly for interactive custom figures; generic exported images. Additional interactive adapters can follow. |
| Machines | Planning here; implementation on bigboss after finalizing the plan. |

Davide described the run setup and inspection mockup as "so far so good." This supports the workflow direction, not a claim that visual polish or every control is finalized.

## 3. Scope and existing foundation

Reuse the execution coordinator, supervisor/process isolation, source/input capture, scheduler, SSH transports, run store, trace readers, scene, clock and export machinery. Preserve their existing reproducibility and observer-invariance contracts.

| Existing area | Required evolution |
| --- | --- |
| CLI and `execution/snapshot.py` | Add project discovery and named-model registration alongside model-file execution. |
| `execution/provisioning.py`, runtime and worker checks | Extend the established locked-environment approach to local project setup. |
| `execution/store.py` | Persist project/model associations, analytical artifacts, saved analyses and presentations; extend reference-aware retention. |
| `kpi.py`, production and logistics collectors | Reuse scalar/series declarations; add explicit population and retained-observation support. |
| `experiments/results.py` | Preserve replication-level inference; allow declared custom outputs and derived analyses. |
| `studio/apps/viewer` | Separate project routes and page responsibilities from the embedded recording viewer. |
| Matplotlib helpers | Provide reusable figures and headless output while preserving ordinary script usage. |
| Documentation | Promote Studio to user-facing navigation and teach complete workflows. |

This work adds no graphical model programming, live process breakpoints, 3D renderer, multi-user server, cloud scheduler or general notebook environment. Existing layout editing, remote workers, comparison and exports must remain accessible. Hosting separate dashboard applications is outside the initial custom-figure integration.

## 4. Opening a project

### 4.1 CLI and first use

The default path is the current working directory. An explicit missing directory is created, including missing parents; failures identify the path and cause. Existing user files are never replaced implicitly.

| Folder state | Result |
| --- | --- |
| Prepared project | Open its overview and saved work; perform background readiness checks. |
| Existing Python project, no Studio declaration | Open setup to identify models and confirm project configuration. |
| Empty/new folder | Offer a small production or logistics starter, or registration of the user's own model. |
| Broken model/dependencies | Open supported saved results and show what needs repair before new execution. |
| Invalid path or unsupported project/store version | Explain the problem and recovery path without modifying unrelated files. |

No simulation starts merely because Studio opened. Model inspection, analysis and simulation code run in execution children, never in the web-serving process. Discovery is limited to the selected project. The setup flow shows entrypoints and configuration changes before applying them.

As a compatibility proposal, an existing `.py` file passed to `studio` retains the released single-model launch path and explains how to register the containing project. Other regular files are rejected. A missing path is always treated as a new directory, not guessed to be a model filename.

The top-level CLI help prioritizes opening Studio, running headlessly, experiments and viewing a recording. It includes short examples and distinguishes replay from opening an existing recording. The internal supervisor command leaves the normal user help. Advanced commands retain their capabilities and dedicated reference documentation.

### 4.2 Project configuration

Use versioned declarations under `[tool.simulatte]` in `pyproject.toml`, preserving existing bundle settings. Register stable model identifiers, display names, entrypoints and per-model layout paths. One model may be designated the default; otherwise Studio presents model selection. Renaming a label does not reassign historical runs.

Models in one project share its Python environment. Projects requiring conflicting dependency environments remain separate projects. Associate legacy results through their stored entrypoint/source identity; ambiguous associations remain explicitly unassigned until the user resolves them, without rewriting the original provenance.

Source, input and output declarations remain explicit. Conventional `models/`, `analysis/`, `data/`, `layouts/` and `experiments/` directories are suggested by starters, not required of existing projects. Analysis entrypoints are registered alongside models, with their accepted inputs and parameters. Exact schema keys and migrations belong in the implementation plan and must preserve these semantics.

### 4.3 Environments

Default projects use an isolated environment and locked dependencies. First-time preparation is a visible, cancellable operation with progress and useful diagnostics. Subsequent launches reuse the prepared environment. Dependency changes and lock updates are explicit operations. A missing uv installation gets a clear setup action/instruction rather than an obscure startup failure.

The project-selected Simulatte runtime must determine the compatible server, assets and execution runtime for new work; do not silently combine incompatible launcher, project or worker versions. The launcher may hand off to project-local Studio after preparation. Existing-environment mode reports its interpreter and reproducibility limitations and does not rewrite that environment on opening.

Viewing supported stored recordings and already generated analysis outputs does not require importing a currently broken model or recomputing analysis. Re-execution requires an appropriate environment. Unsupported stored formats remain explicitly unsupported rather than being silently converted incorrectly.

## 5. Persistent project state

Distinguish three kinds of data:

1. **Authored material:** code, project declarations, inputs, layouts, experiment definitions and reusable analysis definitions. These remain ordinary files that users can version and inspect.
2. **Durable study history:** captured sources/inputs, executions, retained observations, analysis outputs, annotations and saved presentations. Extend the existing `.simulatte/` store for this state.
3. **Rebuildable state:** environments, derived caches and temporary processing files. Mark these separately from durable history and provide distinct cleanup operations.

The existing description of `.simulatte/` as "safe to delete" must be replaced. Deleting it destroys study history; cache cleanup must never perform that deletion. No automatic removal of results is introduced. References from analyses and presentations prevent a run from being silently pruned; the UI explains the dependency and any explicit deletion consequences.

Saving an analysis or presentation changes project metadata, not historical simulation results. A store record owns each named item and its revision history; source definitions remain in ordinary files. Optional exported definitions carry their references explicitly. Missing references after a partial project transfer are diagnosed rather than rebound to different results.

A stopped project can be copied to another machine with its durable store and inputs. Machine-specific environments/caches can be rebuilt. All persisted project-owned references use relative paths or content identifiers. Reopening results is distinct from reproducing a run on another architecture; existing runtime/fingerprint checks still apply. Document stopping/quiescing the writer before copying a project.

Migrations preserve existing request/result identities. A pre-migration backup and actionable failure path are required before changing durable schemas. Retain the single-writer ownership rule; a competing CLI invocation reports the owner and how to proceed. This design introduces no background multi-client daemon.

## 6. Workspace and execution workflows

The sidebar contains Overview, Models, Runs, Experiments, Analyses and Presentations. Settings and storage/environment management are secondary project actions. A page uses breadcrumbs, selection and direct next-action links; no duplicate top-level tab bar.

- **Overview:** project identity, readiness, recent work and useful next actions. An empty project gives a guided first action instead of empty charts.
- **Models:** named entrypoints, defaults, parameters, layout and declared measurements. Layout editing is attached to a model; physical versus presentation changes retain the established distinction.
- **Runs:** searchable history of individual runs with status, model/configuration, seed, recording level and provenance. Experiment replications are grouped with links to their experiment.
- **Experiments:** definitions, configurations, replication progress, failures and numerical comparisons. Worker selection is available in execution settings without dominating the common local flow.
- **Analyses:** named definitions and saved analysis executions, inputs, results and revision history.
- **Presentations:** named views of selected full recordings, their supporting evidence and exports.

The main individual-run flow is Models → Configure run → Run → Inspect results. Show model parameters, seed, stopping policy, warm-up where applicable, recording level and measurements. Starting a run is explicit. Expose finite-population stopping as well as the horizon example in the prototype.

Recording views support playback, entity selection, event stepping, KPIs and diagnosis. Progress and failures remain visible while the user navigates elsewhere; a completed run does not unexpectedly replace the page being read. Browsing a different model does not cancel an active run. Initially retain one project-wide interactive execution slot plus experiment slots; selection and execution ownership are separate.

Source/input edits mark which model's prepared state changed. Explicit-run mode preserves current runs and displayed results. Optional auto-run applies only to the selected development model and cancels/restarts its superseded interactive attempt. Experiments keep their captured inputs. Presentation-only edits update the view without simulation execution.

Closing a browser tab does not own or cancel execution. Stopping the Studio process applies the existing safe execution cleanup and experiment interruption/resume semantics; committed outputs remain available. Playback pause never implies pausing a running simulation process.

## 7. Measurements, populations and retained data

Built-in and custom analysis use the same distinction between a metric, its population and its presentation. Support grouping by recorded attributes and named Python predicates. Overlapping subsets are valid and are labeled as such.

The normal workflow declares measurements and populations before an experiment. Numerical recording includes declared scalar KPIs, series and optional analytical datasets. Detailed analytical records do not require a full animation trace. No blanket capture of every entity attribute is introduced.

Measurement declarations identify fields, types, units and observation semantics. Built-in production examples retain job outcome records with identifiers, lateness and chosen attributes; logistics examples can retain per-AGV state durations and classification. Custom fields are explicit serializable values, not persisted Python objects. Time-resolved analyses require time-resolved observations rather than only final aggregates.

For mutable attributes, declarations specify when membership/values are evaluated: for example at arrival, completion or interval observation. Preserve the existing warm-up, cohort, censoring and stopping contracts. User predicates must remain observational and cannot mutate the simulation; never invoke user predicate code during event construction.

The declared measurement/population definitions and retained-field schema are captured in execution provenance. Analytical configuration changes affect request identity even when simulation parameters are unchanged. Observer changes must still preserve the model trajectory and shared KPI values under the existing invariance contract.

Post-run predicates operate only on retained observations and their associated attributes. A new subset is possible only when those records preserve the needed relationship: a list of categories plus one global average is insufficient. Missing required data is reported before computation when declarations permit validation, otherwise as a clear analysis failure. Studio offers a revised experiment; it does not invent values or silently reconstruct uncaptured state.

Detailed datasets use typed, versioned, chunked storage with bounded writes and paged/streaming reads. The implementation plan selects the codec and Python dataframe adapters; the contract does not require a plotting or dataframe library in the core package. Do not serialize an entire dataset into a websocket message or load it all into the browser.

## 8. Analysis execution and outputs

A reusable analysis definition identifies a Python entrypoint, parameters and required datasets/fields. A saved analysis execution freezes the selected run/experiment identities, definition/source revision, parameters and resolved environment. Its outputs are immutable; recomputation produces another execution under the same named analysis.

Users can run the same analysis through Python, a headless command or Studio. Execute custom Python in a cancellable child with diagnostics and resource ownership, using the execution foundation. The web server never imports analysis code. Output publication uses verified files and atomic indexed visibility, with the same failure and retry discipline as simulation artifacts.

Initial analysis execution is local to the Studio/headless host in the project's environment, with bounded concurrency. Existing remote workers continue to execute simulation replications. Distributed post-processing is not required by this first delivery.

Changing analysis code does not rewrite recorded simulation KPIs. Derived results keep their own provenance and cannot silently replace the original result used for replay verification. Saved analyses stay on their recorded inputs until the user explicitly changes selection or recomputes; no automatic switch to the latest run.

Initial output types are:

| Output | Studio behavior |
| --- | --- |
| Named numerical values | Units, population, missingness and provenance remain inspectable. |
| Typed tables | Sort/filter, page large results and export the underlying data. |
| Distribution data/summaries | Counts, quantiles and optional empirical/bin data; identify retention and approximation. |
| Matplotlib figure | Capture a reusable figure as a saved image/vector artifact, without opening a desktop window in a worker. |
| Seaborn figure | Use the resulting Matplotlib figure through the same path. |
| Plotly figure | Save the figure specification and render with compatible local assets. |
| Generic image | Display supported PNG/SVG artifacts from other tools. |

Native numerical results are the primary analysis view. Charts are optional views of data. Plotting adapters and their dependencies are optional. Keep the existing fast playback chart renderer where appropriate; custom Plotly output does not require replacing all Studio charts. Preserve ordinary Matplotlib script use while enabling explicit figure return/save/display behavior.

For interactive figures, initially support self-contained specifications and browser interactions over their saved data. Python recomputation uses the explicit analysis execution path. Track the required renderer version and package assets locally. Do not inject arbitrary figure HTML or scripts into the privileged Studio document. Additional adapters, such as Altair/Vega-Lite, can use the same artifact contract later.

Experiment inference remains replication-level. A distribution of jobs within a run is descriptive and does not create independent experimental replications. Label pooled versus per-replication summaries, empty populations, failed pairs, sample sizes and interval conventions. Fleet utilization must distinguish the mean of per-vehicle fractions from the ratio of aggregated durations.

Initial reports are named saved analysis results with tables, plots, provenance and user annotations. Full document/slide authoring is outside this first delivery; data and figure export support external reporting workflows.

## 9. From a study to a presentation

An experiment is numerical by default. After selecting a configuration, the user can prepare a separate full recording with a chosen seed. Carry over its model, physical layout, parameters and captured inputs. Using current edited sources is an explicit alternative and produces a distinct run.

Reusing the seed of an existing replication enables the established verified replay path. Choosing a new seed creates a new recorded run; it is not represented as a verified reproduction of a different replication. A mismatch remains visible and does not substitute for the original experiment result.

A named presentation references its recording and stores camera framing, visible labels/entities, selected measurements, time range and playback settings. Presentation edits preserve simulation results. Different views/exports can reuse one recording.

Direct Studio presentation is primary: a clean view, readable labels and measurements, unobtrusive playback controls and a reliable exit back to the workspace. Supporting experiment/analysis results remain directly accessible. Distinguish single-run measurements from statistical findings across replications.

Video and standalone HTML reuse the chosen presentation settings within their documented capabilities. Preserve current codec fallbacks, bounded export, cancellation and offline requirements. If an analysis panel cannot be exported in a target format, explain that before export rather than silently omitting it. HTML export of a simulation presentation is distinct from publishing an arbitrary live Python analysis application.

## 10. UI quality and documentation

Use the selected focused shell as the structural reference. The prototype is evidence of navigation/workflow agreement, not production styling or numerical correctness. Refine the actual UI through a complete working flow before propagating styling across all pages.

Define shared spacing, typography, color, form, table and feedback patterns. Keep the primary action clear; place advanced settings where they can be found without overwhelming first use. Use meaningful names, units and human-readable status. Internal hashes and raw protocol messages belong in diagnostic details. Loading, empty, stale, failed, disconnected and insufficient-data states are designed screens, not browser errors or unexplained disabled buttons.

Keyboard navigation, visible focus, readable contrast, labels, reduced-motion behavior and desktop resizing are acceptance requirements. Check the actual UI at laptop and workstation sizes, including 1280×800 and 1440×900. The main analysis path must work with tables alone. Large histories and datasets use bounded rendering. Preserve the existing playback performance and core overhead gates.

Make Studio a first-class documentation destination rather than placing its primary user guide under Development. Provide:

- Installation and first launch, including launcher versus project environment.
- A short runnable first-project tutorial and an existing-project adoption guide.
- A command overview organized around tasks; a separate complete CLI reference.
- Individual runs, layout editing and inspection.
- An experiment → population analysis → presentation walkthrough using one consistent example.
- Custom measurements, predicates, datasets and Python analysis outputs.
- Project storage, migration, portability, cleanup, dependency repair and workers.

Maintain bare Python tutorials and explain how the same code participates in Studio. Validate documented commands against a fresh project, build the site, check links and verify publication after release. Replace stale capability claims and destructive reset advice as part of the affected pages.

## 11. Delivery boundaries and acceptance

This scope exceeds a cosmetic refresh. Deliver it incrementally; these are proposed work packages, not new release numbers or a task-level implementation plan.

| Package | Reviewable outcome | Dependencies |
| --- | --- | --- |
| P1. Projects and opening | Directory CLI, multi-model declaration, guided setup, environment management, persistent overview and explicit-run behavior. | Released execution/store foundation. |
| P2. Numerical analysis foundation | Population/measurement declarations, retained analytical datasets, versioned Python analyses and output adapters. | P1 project identity and environment contracts. |
| P3. Research workspace | Focused model/run/experiment/analysis pages, iteration, comparisons and understandable failures. | P1; analytical pages use P2. Shell and run flow can be reviewed earlier. |
| P4. Presentation and completion | Saved presentations, evidence links, exports, storage/portability workflows and final visual review. | P1–P3 and existing viewer/export foundation. |

Documentation and regression checks accompany each package. The detailed plan must split work into bounded changes with file ownership, meaningful tests and review gates. Complex dataset/storage and environment transitions require explicit technical designs before coding their interfaces. Code review and browser evidence must cover the real implementation, not only the prototype.

Acceptance scenarios:

1. From a fresh installation, create/open a missing project folder, prepare its environment, run a starter and reopen the saved result. Repeat with no folder argument and an existing project.
2. Register two models with independent layouts; switch models while a run proceeds and find both models' historical results afterward.
3. Open a project with broken model code or missing optional plot packages and inspect supported saved results. Explain which new actions are unavailable.
4. Run an individual model with full recording and inspect entities; opt out of animation and still inspect numerical outputs.
5. Compare job lateness over all jobs and declared custom populations, including distributions and replication-level uncertainty. Verify overlapping/empty subsets and warm-up handling.
6. Compare AGV utilization by family/capability with an explicit aggregation definition and observation window.
7. Define a new post-run subset from retained attributes without rerunning; request an unavailable field and receive an actionable missing-data result.
8. Execute the same custom analysis through Python and Studio; compare numerical outputs and reopen saved Matplotlib/Seaborn and Plotly artifacts offline without rerunning Python.
9. Create a presentation from a chosen experiment configuration and seed; verify original-seed replay or clearly identify a new-seed run. Refine the view and exercise Studio, video and offline HTML delivery.
10. Reopen a quiescent copied project on bigboss, rebuild its environment when needed and preserve history/reference integrity. Clean caches without deleting durable study results.
11. Interrupt execution/analysis or fail publication, then recover without corrupt results or dangling successful records. Protect referenced outputs during manual cleanup.
12. Complete the documented workflow using the installed package. Review the actual UI for visual quality, keyboard use and size constraints; run existing Python, TypeScript, browser, packaging and overhead checks appropriate to affected areas.

Known release follow-ups ([#73](https://github.com/dmezzogori/simulatte/issues/73), [#74](https://github.com/dmezzogori/simulatte/issues/74), [#75](https://github.com/dmezzogori/simulatte/issues/75), [#76](https://github.com/dmezzogori/simulatte/issues/76)) remain separately tracked. Incorporate a fix only when required by this work or separately authorized; do not silently expand the polish scope.

## 12. Relationship to earlier contracts and handoff

On approval, record amendments to the global design and decision log for project-first launch/multiple models, explicit-run default, durable saved analyses/presentations and numerical output extensions. Preserve the established observer, physical layout, trace, replication statistics, authentication and worker safety contracts. Schema migrations are versioned; old result identities are not recomputed merely because the workspace has new names or routes.

This specification does not declare 1.0 readiness. Release numbering and integration remain maintainer decisions. Completion requires the acceptance evidence above in addition to feature implementation.

Before execution on bigboss, the approved design and reviewed implementation plan must be committed and available there. The handoff records the exact branch/commit, mockup, accepted decisions, intended work packages, checks and outstanding risks. Inspect bigboss's checkout/worktree and available tooling before preparing an isolated implementation branch; preserve unrelated work. Run builds and substantial verification there. No Herdr or other agent orchestration is implied by moving development machines.

## References

- [Matplotlib figure saving](https://matplotlib.org/stable/api/figure_api.html#matplotlib.figure.Figure.savefig).
- [Seaborn's Matplotlib foundation](https://seaborn.pydata.org/).
- [Plotly figure JSON](https://plotly.com/python-api-reference/generated/plotly.io.to_json.html) and [interactive HTML export](https://plotly.com/python/interactive-html-export/).
- [Altair JSON/HTML output](https://altair-viz.github.io/user_guide/saving_charts.html), a later adapter candidate.

Research supports the output mechanisms; no new adapter has been implemented or verified in this session.
