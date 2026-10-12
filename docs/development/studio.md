# Studio: local execution and recorded simulations

Studio opens a `.simtrace` recording without running the model. Install the optional
server dependencies and open a recording:

```bash
pip install 'simulatte[studio]'
simulatte view run.simtrace
# Equivalent: python -m simulatte view run.simtrace
```

The command binds only to `127.0.0.1`, selects an unused port and opens your browser.
Use `--port 8765` for a fixed port or `--no-browser` to print a one-use access link.
Keep that link private. In an interactive terminal, enter `link` to create a fresh
link if browser launch failed or another tab needs access. Reloading the original
tab works; copying its token-free address to a new tab does not grant trace access.
Ctrl-C stops the server. A recording changed on disk must be reopened.

The read-only viewer is available starting with 0.15. Local model execution,
parameter/layout editing and live playback are available starting with 0.16.
See the acceptance report for platform-specific verification evidence.

## Make a recording

Record a model with `TraceRecorder` as described in
[events and traces](../guides/events-and-traces.md). Full recordings include entity
state, domain events, inspect-only observer events and recorded KPIs. KPI-only
recordings provide charts/results but no entity animation. Both retain resolved
layout metadata when available.

The repository includes two small examples:

```bash
uv run python examples/studio_recording.py /tmp/shop.simtrace --model shop
uv run python examples/studio_recording.py /tmp/fleet.simtrace --model fleet
uv run --extra studio simulatte view /tmp/fleet.simtrace
```

## Navigate and inspect

The scene uses recorded equipment placements and motion. Without layout metadata,
static entities use a labelled viewer fallback layout. Unlocated entities appear
in an unplaced tray; invalid or approximate motion is identified. Decorative
transfer trails are not job positions. Pan by dragging; use Fit, zoom or Focus
selected to navigate. The entity list provides selection without canvas picking.

Play and the slider use continuous simulation time. Previous/Next event inspect
exact `(time, sequence)` positions, including distinct events at the same time.
Preparation history is separate. Non-finite event times remain inspectable but do
not enter playback arithmetic. Hidden tabs pause advancement without catching up
on return. Space toggles playback outside text controls and export dialogs.

The inspector shows paged fields and nested values, plus historical state for a
selected retired entity. Events can be filtered by time, type, changed entity or
payload text. “Changed entity” means a delta target, not every entity mentioned in
an arbitrary payload. Older recordings may contain domain-event history only.

Add up to eight recorded KPI series. Metadata shows units and declared display
rules. Exact-event values never look ahead. Continuous linear interpolation is
labelled; missing values follow the declared gap/hold rule. Older traces assume
previous-sample interpolation with gaps. End-of-run results remain separate from
playback values. Studio does not recalculate simulation KPIs.

Panels can be resized and docked. Reset panels restores the default arrangement;
only panel preferences are stored locally. Present hides inspectors and charts.
Trace data is never saved as a viewer preference.

## Share a recording

**Export HTML** creates a standalone read-only player that opens from `file://`
without a server or network. It contains the complete original recording,
including labels, payloads and metadata. Review that content before sharing.
Studio warns at 25 MiB of trace data and refuses traces above 100 MiB or an HTML
file above 150 MiB. Use local viewing or a video for larger recordings.

**Export video / PNG** renders a separate, fitted scene without moving the viewer.
Choose a finite interval, simulation units per second, 24/30/60 fps, background,
time overlay and even dimensions up to 3840 × 2160. Default resolution is 1920 ×
1080. MP4 requires H.264 encoding; WebM probes VP9 then VP8. If the selected codec
is unavailable, the error offers another format. PNG requires no video encoder.
Codec support depends on browser, platform and resolution.

Exports include the exact end state held for one frame. The frame count is
`ceil((end-start)*fps/rate)+1`, so a zero-length interval produces one frame.
Video is limited to 100,000 frames; PNG archives to 10,000. PNG ZIP64 archives
contain numbered images and `timing.json` with the same timing formula. Large
exports stream to disk instead of accumulating the whole output in memory.

Where available, the browser asks for a destination file. Otherwise Studio stages
the output in origin-private browser storage and offers **Download** and **Remove
temporary copy**. Keep the dialog open until the download finishes, then remove
the temporary copy. Closing the dialog also attempts removal. Cancellation stops
the export and removes owned staging files; an interrupted user-chosen file may
remain incomplete. Storage/cleanup errors are shown explicitly.

Temporary storage belongs to the exact `127.0.0.1:port` origin. A crash can leave
files behind. Studio offers removal of abandoned copies on that same origin,
without deleting an active export in another tab. A new random port cannot clean
the old origin; use browser site-data settings to remove it. Another service later
using that origin could access residual files. With neither supported disk API,
only PNG has a bounded, 64 MiB in-memory fallback.

Chromium and Playwright WebKit have exercised OPFS exports and offline playback;
Firefox has been exercised on Linux; WebM is its verified interoperable video
alternative (its H.264 encoder metadata produced FFmpeg parser warnings). Actual
Safari has passed manual export and offline-player checks. These results do not
promise every codec on every operating system.

## Development and verification

Use Node.js 24 and the pnpm version declared in `studio/package.json`:

```bash
pnpm --dir studio install --frozen-lockfile
pnpm --dir studio dev
```

Open the printed loopback address and choose a local recording. The fixture
`tests/fixtures/traces/viewer/viewer.simtrace` contains observer events,
duplicate-time samples, missing data and entity retirement.

```bash
pnpm --dir studio typecheck
pnpm --dir studio test
pnpm --dir studio build
pnpm --dir studio exec playwright install chromium firefox webkit
pnpm --dir studio test:browser
uv run python studio/scripts/package_assets.py
uv build
```

Editable Python installs need no frontend build for simulation use. The view
command reports missing/stale assets with the build command. Release wheels and
sdists include hashed assets, the player and dependency notices; the build hook
rejects stale inputs. Building a wheel from an sdist needs no Node or frontend
network access. See the repository’s `CONTRIBUTING.md` for the contribution workflow.

| Package | Responsibility |
| --- | --- |
| `packages/trace` | Binary reader, range sources, event/sample queries and replay |
| `packages/playback` | Prepared frames, clock and KPI eligibility |
| `packages/scene` | Recorded motion, projection, Pixi rendering and camera |
| `packages/export` | Manual export schedule, streamed media and offline HTML |
| `apps/viewer` | React shell, docked panels and imperative chart/inspector adapters |
| `apps/player` | Bundled offline player |

Production browser checks enforce a restrictive content security policy and test
saved HTML with networking disabled. The acceptance evidence in `specs/research/`
records measured workloads, limits and outstanding platform checks.

## Model metadata and stopping (0.16)

The optional `@simulatte.model` decorator describes a build function without
wrapping it: ordinary Python calls keep working. Use a frozen dataclass for
parameters. Supported fields are bool, safe integer, finite float, string,
scalar Literal and Enum. Annotated fields accept Range (inclusive bounds),
Length (Unicode code points) and Description. Enum command-line values use
member names; defaults must be members. Default factories are unsupported.

```python
from dataclasses import dataclass
from typing import Annotated
import simulatte


@dataclass(frozen=True)
class Params:
    rate: Annotated[float, simulatte.Range(0.1, 2.0)] = 0.8


@simulatte.model(params=Params, horizon=1000, warmup=100, time_unit="min")
def build(env, layout, params):
    # Attach components and schedule processes here; do not run the environment.
    pass
```

`get_model_spec(build)` returns immutable metadata. The decorator alone does
not start workers, create a run store or require Studio dependencies. Core
`Environment(parameters=..., artifacts_dir=...)` records supplied parameters
and exposes an optional output directory; ordinary scripts retain their defaults.

Stopping policies are `Horizon(until, warmup=0)` or
`FinitePopulation(arrivals=N, safety_limit=S)` /
`FinitePopulation(cutoff=T, safety_limit=S)`. Finite models explicitly bind
`Population(env, policy)` in scripts (the execution driver supplies the policy
in managed runs), admit unique work keys and complete them after their final
transition. Bracket scheduled arrivals with `pending(key)` and `unpending(key)`.
`admit` returns false after closure; generators must abandon pending arrivals
when `population.closed` fires. A finite run succeeds only after closure and
zero active/pending work, without waiting for unrelated housekeeping timers.
Arrivals exactly at a time cutoff are refused; completion exactly at the safety
limit is accepted. Failure to drain is an error. These are model-control
operations, not collector observations.

The following managed workflow is available in 0.16. Plain Python scripts and `Runner`
keep their existing execution path; adding metadata does not opt a script into
managed execution.

### Execution isolation foundation

Managed execution captures current working-tree bytes, including dirty tracked
files. Git projects start with tracked membership; non-Git projects must declare
`[tool.simulatte.bundle].include` in `pyproject.toml`. Additional include/exclude
patterns and project-relative `python-path` roots are supported. Source, declared
data inputs and the configured layout are separate inputs. Declared data must be
read during build or simulation, so metadata inspection can import the model
without reading it.

The internal worker uses disposable `-I -S` children. It does not execute `.pth`
or startup hooks; supported local imports come from the captured tree. Workspace
and installed local-package metadata determine import roots, and external local
packages are rejected. A development installation of the trusted Simulatte runtime
is frozen separately. Read-only materializations protect against accidental
writes; they are not a security sandbox.

Worker framing, diagnostic buffering and controls have fixed bounds. Native
stdout/stderr is drained separately from protocol traffic. Heartbeats belong to
the supervisor, so an unresponsive model can still be cancelled. POSIX cleanup
uses an independent guardian and process groups, including cleanup after parent
exit; deliberately detached sessions are outside this guarantee. Windows uses
suspended creation and Job assignment before execution, and refuses assignment
failure. Native Windows acceptance is still pending; API unit checks are not a
substitute for that evidence.

Worker trace output now uses durable segments at existing record boundaries.
Temporary reservations and committed files share a 256 MiB / 1,024-file quota;
only validated acknowledgements release space. A full spool blocks recording
without dropping events, and blocked writes remain cancellable. The ordinary
file recorder retains its existing trace bytes.

The local run-store foundation uses one coordinator owner, durable segment
receipts and fenced result publication. Partial uploads stay private; a result
becomes visible only after trace verification, execution cleanup and the final
index transaction. Recovery removes unindexed output rather than adopting it.

Managed run and Studio use these same execution components. Each attempt gets
a fresh execution process; the local supervisor is reused, with no warm spare.

## Parameter and layout editing (0.16)

The `simulatte studio MODEL.py` command serves the local editor.
It inspects a decorated model in a fresh process and runs once when the declared
inputs and parameter defaults are valid. Missing required parameters open the
form without starting a run. `simulatte run MODEL.py` uses the same execution
inputs for a single headless run; ordinary scripts and the read-only `view`
command remain available.

Open **Parameters** to edit typed values, the decimal seed, stopping policy,
recording level and KPI keys. Typing does not start a run. **Apply and run**
submits one revision. When another tab or a source edit changes the inputs,
review the new revision before keeping your draft or reloading authoritative
values. Removed fields and incompatible types stay visible as validation issues.
An empty required string must be supplied explicitly.

Open **Layout** for structured SP2 controls and a draft canvas. Shift-click or
use the handle list for multiple selection; dragging commits one draft gesture
on pointer-up. Numeric movement and rotation work without dragging. Coordinates
use the existing y-up convention and z=0. Snap uses the declared grid. Vertex
insertion keeps existing lane vertex ids; segment overrides never move to new
segments automatically.

**Validate layout** checks presentation changes without executing the model.
Physical changes use a fresh disposable preparation process and stop at the
ready barrier, without stepping the simulation or publishing a run. Validation
does not write the working file or replace an active run. **Apply layout** saves
through the revision check and atomic replacement: physical changes run again;
presentation-only changes update the view association. A failed validation
retains the draft and the prior view.

**Reset** removes a file override and inherits the code or automatic value.
**Remove optional value ($unset)** suppresses an inherited optional value.
The provenance table describes the last resolved layout, including diagnostics.
Reassign or remove orphan entries explicitly. Generated entity ids may be used
for presentation overrides with a warning; physical overrides require explicit
names in Python.

Undo and redo submit inverse or repeated patches against the latest revision.
They preserve unrelated edits and reject overlapping changes. History is kept
in the browser, bounded to 64 entries and 32 MiB; reloading the page does not
restore that local history. Layout files retain the existing 16 MiB limit.

### Live playback and recovery

Studio opens a verified live prefix when the worker reaches INITIAL. Playback
starts at activation. **Follow live** moves to the newest committed simulation
time; seeking or stepping returns to manual playback. New live prefixes preserve
playback intent, including while a frame is loading; a manual seek stays paused.
The execution progress line separately reports simulation time, event count,
worker liveness, stalled
execution and trace-storage backpressure. Run diagnostics retain a bounded tail
and identify the originating revision and attempt.

**Run history** selects one of the latest 20 committed attempts with its recorded
layout, clearing any presentation edits even when that run is already open.
Selection stays pinned until **Follow live**. The current presentation
can change without rewriting the trace or rerunning the model. HTML, video and
PNG exports pin the selected committed run and presentation; a later edit or
run cannot change an open export dialog. Live export becomes available only
after the result is committed. Offline HTML contains the original trace bytes
plus separate presentation metadata bound to their SHA-256 digest.

**Rescan / retry** checks source and declared inputs and can retry a failed
initial inspection. Returning focus to the browser also checks for changed
files, but does not retry or rerun unchanged inputs. If the initial layout JSON
is invalid, the Layout editor offers a repair field. Saving preserves the exact
previous bytes in `.simulatte`, refuses a changed disk/revision baseline, and
restarts normal inspection. Invalid layouts are never silently replaced by
empty defaults for execution.

## Run a decorated model

Core-only installation is sufficient for a headless run:

```bash
pip install simulatte
simulatte run examples/studio_model.py --param servers=3 --seed 17 --json
simulatte run examples/studio_finite.py --arrivals 8 --safety-limit 100
```

From a source checkout, prefix commands with `uv run`. Install `[studio]` for
interactive editing:

```bash
pip install 'simulatte[studio]'
simulatte studio examples/studio_model.py --no-browser
```

A file containing multiple decorated models requires `MODEL.py:build_name`.
Build functions receive `(env, layout, params)` and must finish preparation
without advancing simulation time. Complete layout/component wiring there;
schedule generators with `env.process`. The worker activates and runs the
prepared environment only after the coordinator accepts durable initial state.

Use repeated `--param name=value`, `--seed`, `--horizon`/`--warmup`, or
`--arrivals`/`--cutoff` with `--safety-limit`. `--level kpi` records KPI-only
output; `--kpi KEY` selects KPI keys. `--timeout SECONDS` bounds the whole attempt,
including preparation. `--output PATH` copies the committed trace and refuses to
overwrite an existing file. `--json` prints one result envelope; diagnostics go
to stderr. Exit codes are 0 for success, 2 for validation failure, 1 for execution
failure and 130 for cancellation.

### Source and declared inputs

A project is discovered from the model path or selected with `--project DIR`.
Dirty tracked Git files are included; an untracked helper needs an explicit
include. Outside Git, declare membership in `pyproject.toml`:

```toml
[tool.simulatte.bundle]
include = ["model.py", "helpers/*.py", "data/rates.json"]
exclude = ["scratch/**"]
python-path = ["."]
```

Use `@model(params=Params, horizon=100, inputs=("data/rates.json",))` to declare
simulation data. Read it inside `build`, relative to the captured model file,
for example `Path(__file__).parent / "data/rates.json"`. Import-time reads of
declared data are rejected. External paths, special files and symlink escapes
are rejected; in-project links are materialized as bytes. Captures are capped at
1 GiB and 100,000 files, retry observable races three times and report
`snapshot_changed` when they cannot obtain a stable capture. Timestamps are not
part of input identity. The configured `layout.json` is captured separately and
cannot also be a declared input. Use `--layout PATH` to select another layout.

Imports from the project must resolve to captured files. Models in regular or
namespace packages support relative imports; their package name is relative to
the closest enclosing `python-path` root, including a configured `src` root.
Installed dependencies must be available in the selected Python environment;
external editable sources
and unverifiable loaders are refused. Adding or deleting a module can change
input identity, including negative dependencies. The watcher combines native
notifications with content rescans, so atomic saves and same-mtime edits matter.

Physical/source/input changes cancel obsolete attempts and schedule the newest
valid revision. Presentation changes use the recorded layout dependency closure.
If the model read a visual field, changing it is a simulation input and reruns.
Changes to unread labels/colors or positions classified as presentation update
the view without changing the original request or trace. Invalid source/layout
pauses admission and keeps the last committed run available with its origin label.

### Results, reproduction and side effects

The minimal `.simulatte` store holds snapshots, captured runtime documents,
revision/attempt records, durable receipts and committed results. A single local
coordinator owns the store at a time; close Studio before starting a headless run
in the same project. There is no retention/garbage-collection UI in this release.
Do not edit its index or move individual internal files. Partial attempts are
never adopted as successful results after a crash.

Write managed output under `env.artifacts_dir`; published artifacts are copied
and checked with the result. Arbitrary external writes, network services, wall
clock values and unbound randomness are outside reproducibility guarantees.
Process isolation prevents accidental module reuse; it is not an untrusted-code
sandbox. Normal descendants are cleaned up on completion/cancel; deliberately
detached POSIX sessions are outside containment.

Use the request id from the JSON envelope to reproduce captured inputs:

```bash
simulatte run --reproduce REQUEST_ID --project /path/to/project --json
```

This creates a new attempt and compares fingerprint and artifacts against a
committed baseline. It does not read the current working model, but requires a
compatible runtime/dependency environment. Incomplete provenance or a mismatch
fails verification rather than claiming success.

### Limits and recovery

A slow/disconnected browser cannot acknowledge worker data or determine success.
The coordinator durably receives output independently. Worker spool bounds are
256 MiB and 1,024 files with at most 16 MiB of unacknowledged transfer payload.
No events are dropped to regain space. No durable credit while a full spool is
blocked for 60 seconds produces `disk_full`. Diagnostic tails are bounded to
1 MiB per stream; the UI shows a smaller tail. Progress heartbeats can continue
while simulation progress is stalled.

Cancel targets an exact attempt. A stale tab must reload authoritative revision
state before saving; reconnection never replays mutation commands automatically.
Conflicting external edits preserve the draft and backup bytes. An uncooperative
editor can still race the final check/rename interval; recoverable backups make
that filesystem limitation explicit. Closing Studio cancels owned work and
cleans descendants before releasing the store owner lock.

SP4 is local single-run execution/editing. Experiments, statistical comparisons
and SSH workers remain outside this release.

Request ids include the captured layout revision. Identical model bytes and
parameters reached through different revision histories can therefore have
different request ids, while producing the same semantic fingerprint. An exact
reproduction retains the stored request identity and creates a new attempt.
