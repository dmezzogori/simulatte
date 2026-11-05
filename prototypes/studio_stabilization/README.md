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

## Two-hour gate after chart batching

Prepare both host environments as above with the same chart-batching core
wheel and each host's verified native companion. Set `TMPDIR` to a private
directory on disk before running `prepare.py`; the new project and its
results live there. Keep the preparation and environment metadata
(`environment.py`) together as `{macos,linux}-{prepared,environment}.json`
in a private evidence directory on the browser host.

On Linux, put the Studio study's `serve.py` and `audit.py` in
`<remote-root>/studio/`. From the browser host, run:

```sh
python prototypes/studio_stabilization/run_endurance.py \
  --output <evidence-dir> --remote-host <ssh-host> --remote-root <remote-root> \
  --local-port <port> --remote-port <different-port> \
  --assets-sha256 <installed-manifest-sha256> --seconds 7200
```

The supervisor starts two loopback servers and forwards the Linux port.
Chromium stays on one document per host for the full run. Firefox and
WebKit reload in rotation. All three engines reconnect and seek/follow.
There is no injected HTTP delay. The supervisor stops if the browser host
has less than 8 GiB of free space. After the timed runs it checks revoked
prefixes, stops the servers, audits the result stores, and writes
`report.json`. `status.json` and `browser.log` show progress; an interrupted
or failed audit is recorded explicitly.

The gate requires exact reads or an explained revocation/abort, no
unexplained browser errors or trace alerts, no partial chart panels and
eight charts in more than 75% of live samples in each browser. Each model
and retention mode must publish a valid result; repeated and shared
configurations must agree on control state and KPIs. The report separates
retained store growth from process resources. It compares the second
ten-minute window with the last two and flags sustained median increases
of at least 8 MiB in post-GC Chromium heap, 32 MiB in server RSS, or eight
server descriptors. These thresholds flag changes to investigate; the
trial cannot establish leak freedom.
