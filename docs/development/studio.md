# Studio: viewing recorded simulations

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

Studio is available starting with 0.15. Actual Safari MP4 output, export cancellation
and cleanup, and standalone offline playback have passed manual acceptance.

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

## Model metadata and stopping (0.16 development)

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

The execution and interactive Studio commands are under development; this
metadata API does not imply that the full SP4 workflow has shipped.

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

These are internal execution foundations. The managed run driver, Studio editing
and live viewer integration remain separate implementation work.
