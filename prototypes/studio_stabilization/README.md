# Focused Studio stabilization

This study follows the representative-model gate at `196fcc9`. It uses the same
native production workloads and companion wheels. It investigates live trace
reads and chart refreshes; it does not select deployment baselines or measure
engine speed.

Prepare a fresh installed environment with
`prototypes/native_stabilization/prepare.py`, using the baseline core wheel or
the candidate built with `pnpm --dir studio build`,
`python studio/scripts/package_assets.py`, and `uv build --wheel`.
`serve.py --work <work> --output <private-server-log-dir> --port <port>`
wraps the real packaged Studio server prepared by that script. For a remote
host, forward its loopback port to the same port on the browser host.

For the controlled trials, add `--latency-ms 25` to each server. A one-second
pause after response headers is enabled only for the cancellation probe. Run
`node prototypes/studio_stabilization/study.mjs --targets <private.json>
--engines chromium --probe cancel`, then repeat with WebKit. Targets are
`{name, access, output}` objects; `access` is a private path to the prepared
server's `access.json`. Never publish that file. For charts, use all three
browsers, `--seconds 100 --budget 4096 --sample-ms 350`. The targets file is a
JSON array. For the final soak,
use fresh environments, no injected latency, and `--seconds 900`.

The browser and ASGI logs share request identifiers and record prefix identity,
range, timing, planned and sent lengths, revocation and abort context. The
browser's actual body reader also reports bytes received; a runner-level
`requestfailed` event is not sufficient evidence of a failed body.

After the soak, use `revocations.py` with the private access file and browser
summary to check every bad prefix while Studio still listens. Then stop the
server with a `stop` marker in its private work directory. Run
`audit.py --project <work/project> --browser <summary.json>
--output <audit.json>` with the installed Python. It checks the real run store,
trace structure and KPI endpoints, and verifies that failed/cancelled attempts
have no result. `analyze.py` reconciles the collected evidence. The raw logs
remain in ignored `.build/` directories; only the compact audit and evidence
summary belong in version control.
