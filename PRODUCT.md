# Product

<!-- impeccable:product-schema 1 -->

This record covers **Simulatte Studio**, the optional local workspace shipped with the Simulatte Python library (`pip install 'simulatte[studio]'`). The library and the simulatte.dev documentation site are context here, not design surfaces.

## Platform

web

## Users

- **Academic researchers** in production planning and control and intralogistics (the maintainer's group and other labs). They run studies over weeks, compare policies across replications and write papers from the results.
- **Students** learning discrete-event simulation and Simulatte, in courses or theses. They need a first successful run without understanding execution internals.
- **Industry consultants** modelling a client's plant or warehouse, iterating on the model and then presenting results to that client.
- **Open-source users** who install `simulatte[studio]` from PyPI on their own, with no one to ask.

Receivers of presentations and exports are a second audience: **industrial clients** (plant and logistics managers, usually not specialists in simulation statistics) and **academic peers** (conferences, lab meetings, thesis committees).

The three supported uses, daily research, onboarding and client presentation, carry equal weight. No one audience is served at the expense of the others.

## Product Purpose

Studio gives Python simulation models a persistent project workspace: open a folder, register named models, prepare a managed environment, run models, inspect recordings, run experiments across replications, analyse populations and measurements, and turn chosen results into presentations that can be shown in Studio or exported.

Success means a researcher can do sustained professional work in it, a newcomer reaches a first successful run unaided, and a presentation built in it can be put in front of a client or a committee without rework.

## Positioning

Against GUI simulation suites such as AnyLogic, FlexSim or Simio:

- **Code stays the authority.** Models are plain Python on SimPy. Studio never replaces code and everything also runs headless; there is no graphical model programming.
- **Statistical rigour by default.** Results are replication-level, with t intervals, Bonferroni families, paired differences by replication, provenance and verified replay.
- **Local and open.** Runs on the user's machine or their own SSH workers, with no cloud service or license server. Open source.
- **Evidence to presentation.** Presentations are built from saved, traceable results, not screenshots pasted into slides.

## Operating Context

- Launched from a terminal with `simulatte studio [folder]`. Serves on loopback only; access is through a one-use link, and remote use goes through an SSH tunnel. Single user, no accounts.
- Used in desktop browsers (Chrome and Safari observed) alongside an editor where the model code lives. Users switch between editing Python and inspecting results in Studio.
- Long-running work is normal: environment preparation, multi-replication experiments over several execution slots, remote workers.
- Presentations are shown live from Studio, or exported as PNG, MP4 or a standalone offline HTML file.

## Capabilities and Constraints

- Navigation is a persistent project sidebar with focused pages. A separate work-tab bar was proposed and rejected.
- Opening a project never starts a simulation or imports a model. Execution is explicit (Run); experiments always need an explicit start.
- Model inspection, runs, experiments and analyses run in child processes in the project environment, never in the web server.
- Custom figures come from Matplotlib (including Seaborn) and Plotly; native numerical outputs must be useful without any plot.
- Out of scope: graphical model building, live breakpoints, 3D rendering, multi-user server, cloud scheduler, general notebook environment.
- Terminology users see: project, model, run, recording (full or KPI-only), experiment, configuration, replication, population, measurement, analysis, presentation, evidence, environment, slot, worker. Internal names (execution request, transport, store schema) stay out of the UI.
- APIs and formats are under active development and may change between releases.

## Brand Commitments

The **Simulatte** name is binding. Studio's visual identity is free and is not tied to the simulatte.dev site or the logo (`docs/assets/logo.png`).

## Evidence on Hand

- User documentation: `docs/studio/` (projects, environments, experiments, measurements, analysis, presentations, storage, CLI).
- Product design spec: `specs/2026-10-19-studio-project-experience-design.md`; accepted workspace mockup: `specs/mockups/2026-10-19-studio-project-workspace.html`.
- Manual QA feedback and screenshots: `specs/research/2026-10-23-studio-visual-qa.md` (paused review; functional and polish findings, severities unassigned).
- Production and Intralogistics starters and the demo fixtures used in tests. No customer names, case studies, testimonials or usage figures exist; do not invent them.

## Product Principles

1. **Python remains the source of truth.** Studio reflects and runs the user's code; it never becomes a second place where model logic lives.
2. **Numbers before pictures.** Every result is readable as numbers with its uncertainty; plots and animation add to that, never replace it.
3. **Traceable by construction.** Anything shown, compared or presented can be traced to the run, seed, inputs and environment that produced it.
4. **Nothing happens implicitly.** Runs, updates, deletions and environment changes are explicit and visible, with their state kept current.
5. **One workspace for the expert and the newcomer.** Depth for daily research must not cost the first-run path, and presentation quality must not require leaving Studio.

## Accessibility & Inclusion

WCAG 2.2 AA is the floor, including contrast, full keyboard operation, visible focus and respect for reduced-motion preferences.
