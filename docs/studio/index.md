# Studio

Studio is an optional local workspace for Python simulation projects. Open a
folder, register named models, prepare its environment and choose when to run.
Opening a project never starts a simulation or imports a model.

```bash
pip install 'simulatte[studio]'
simulatte studio                 # current folder
simulatte studio studies/plant   # opens or creates this folder
simulatte studio plant.py:build  # register an existing Python entrypoint
```

Studio listens on loopback only. `--no-browser` prints a one-use access link;
`--port 8765` selects a port. Keep the access link private. Ctrl-C closes Studio.
An interactive terminal accepts `link` to issue a new browser link.

## First run

1. In an empty folder, choose **Production** or **Intralogistics**. Review the
   proposed files and apply them. Existing files are never overwritten by a starter.
   Alternatively, choose **Register an existing Python model**.
2. Choose **Prepare environment**. Review and apply the proposed lockfile, then
   prepare again. Studio installs a separate immutable environment. Choose
   **Use environment and restart** to open Studio in that runtime.
3. Open a model and choose **Prepare model**. Inspection happens in a separate
   child process. Review its parameters, seed, stopping policy and recording level.
4. Press **Run**. The run page shows progress and, for full recordings, the existing
   scene, entity inspector, recorded KPIs and event filters.
5. Give useful runs a name or notes. Close Studio and reopen the folder: **Runs**
   retains the saved history, without starting another run.

The production starter also runs as ordinary Python:

```bash
uv run python models/production.py
uv run simulatte run models/production.py:build --seed 29
```

The Python API, `Runner`, headless experiments and `simulatte view run.simtrace`
remain available independently of the workspace. Model code does not import Studio.

## Navigation and recordings

The sidebar owns navigation: **Overview**, **Models**, **Runs**, **Experiments**,
**Analyses**, **Presentations** and **Settings**. Analyses and saved presentations
are reserved for later packages; existing recording presentation and HTML/video/PNG
export remain on the run page. Existing experiment and comparison tools remain
available under **Experiments**.

Selecting another model preserves its draft and does not start or cancel a run.
If the interactive slot is occupied, Studio identifies the owner. Inspect that run,
wait, or explicitly choose **Stop and run this model**. Cancellation and admission
recheck the owner, so another tab cannot accidentally replace a newer run.

See [projects and history](projects.md), [environments and recovery](environments.md),
and the [recording controls and export reference](../development/studio.md).
