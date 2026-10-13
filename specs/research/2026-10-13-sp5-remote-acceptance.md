# SP5 remote acceptance on personal hosts (2026-10-13)

Branch `feature/sp5-experiments` at `8d0a7a6`, coordinator on eva (macOS arm64, CPython 3.14.1, uv 0.13.0). Workers: strangelove (Linux x86_64, uv 0.9.5), otacon (Linux aarch64, uv 0.12.16), bigboss (macOS arm64, no uv). teban was not touched. Raw data: `specs/research/sp5-data/remote/` (worker config used: `workers.toml`; helper scripts `sp5_dump.py.txt`, `sp5_replay_api.py.txt`, `sp5_cancel.py.txt`). The worker file was a temporary one in `/tmp` selected with `SIMULATTE_WORKERS`; `~/.config/simulatte/workers.toml` does not exist and was not created.

## Summary

| Area | Result |
| --- | --- |
| `workers check` / `setup --install-uv` refusal | Measured, as designed |
| Remote run of the committed fixture `examples/uv_project/` (path dependency) | **Fails on every remote host**: defect D1 (path dependency) and, on strangelove, D2 (exact Python pin unavailable) with D3 (error hidden and retried) |
| Remote run of a plain-lock variant (no path dependency, pin 3.14.0) | 8/8 succeeded on local + strangelove + otacon in 12.5 s |
| `simulatte replay` on a remote request | **Not usable from the CLI**: defect D4 (`no_compatible_worker` for the original host). Through the coordinator API: strangelove `verified`; otacon `unverified (runtime_mismatch)` |
| Fault injection (ssh killed), cancellation over SSH, `workers prune` | Measured, as designed |

Status of the task: the remote path works end to end for projects without in-root path dependencies; the fixture the brief specified cannot run remotely until D1 (and, for strangelove, D2) are fixed. Gates that remain open are listed at the end.

## Fixture

`examples/uv_project/` (committed): `pyproject.toml` (name `uv-project-example`, `requires-python >=3.11`, `simulatte==0.16.0`, `shopkit` through `[tool.uv.sources] shopkit = { path = "packages/shopkit" }`), `packages/shopkit/` (hatchling, `src/shopkit/__init__.py` with `scenario_servers`), `plant.py` (adapted `studio_model.py`, horizon 60, imports `scenario_servers`), `sweep.experiment.json` (servers 3 and 5, 4 replications, `kpi`, workers `local, strangelove, otacon`), `.python-version` = `3.14.1`, and `uv.lock` (30 171 bytes, 8 packages, `simulatte` 0.16.0 from PyPI; created with `uv lock` inside the directory). The repo `.gitignore` ignores `.python-version`; it is added with `git add -f`. `examples/` is not excluded from the sdist (only `uv.lock` is); the fixture is about 31 KB plus small sources. A local smoke run (`--workers local`, 8/8 succeeded) was run first with `uv run` from the repository environment; its store was moved aside so that the remote runs started with no reusable results.

## Step 1: `workers check` and `setup`

Files: `check-*.json|txt`, `setup-bigboss*`.

| Worker | Exit | Platform | Shell | uv | Cache |
| --- | --- | --- | --- | --- | --- |
| strangelove | 0 | Linux x86_64 | /bin/bash | 0.9.5 | writable |
| otacon | 0 | Linux aarch64 | /bin/bash | 0.12.16 | writable |
| bigboss | 1 | Darwin arm64 | /bin/zsh | missing | writable |

`workers setup bigboss --install-uv` printed `Would run on bigboss: curl -LsSf https://astral.sh/uv/install.sh | sh`, the prompt `Install uv on bigboss with the official installer? [y/N]`, received `n` on stdin, printed `uv not installed` and exited 1 (human and `--json` forms; in JSON mode the prompt goes to stderr and stdout stays one document). `ssh bigboss 'which uv || echo none'` before and after: `none`. `workers check` creates `~/.cache/simulatte` on the host (the writability probe).

## Step 2: remote experiment runs

### 2a. The committed fixture (`examples/uv_project/`), `--workers local,strangelove,otacon --timeout 1800`

Run `cd46ddf8…` finished in 10.3 s with `status: completed`, counts succeeded 5, failed 1, lost 2 (`run1.json`, `run1-store.json`).

- Local slots: all 5 local replications succeeded (1.0 to 1.5 s each).
- otacon: `provisioning_failed`, `uv sync exited with 2`, `error: Failed to generate package metadata for shopkit==0.1.0 @ directory+packages/shopkit ... Distribution not found at: file:///home/davide/.cache/simulatte/envs/<hash>.build/packages/shopkit`. This is defect D1.
- strangelove: all 6 attempts `lost` with `Worker handshake failed` (3 dispatches per replication, then the replication stays `lost`). Cause found by running the bootstrap by hand: `error: No interpreter found for Python 3.14.1` (D2, D3).
- Repeating the fixture run with the pin changed to 3.14.0 on strangelove only (`run5-*`): 8/8 failed with the same D1 error, so D1 is host independent.

### 2b. Plain-lock variant (all remaining measurements)

To exercise everything else, a variant outside the repository (`/tmp/sp5-plain`, its own git repo, deleted afterwards) was used: the same `plant.py` without `shopkit`, `pyproject.toml` depending only on `simulatte==0.16.0`, `.python-version` = `3.14.0` (strangelove's uv 0.9.5 cannot provide 3.14.1), a fresh `uv.lock`. Same experiment file.

Run `365ec150…`, local,strangelove,otacon, cold caches for the environment and source blobs (the trusted runtime was already staged on both hosts from 2a): 12.5 s wall, 8/8 succeeded, 4 per configuration, `mixed_runtime: true` on both configurations (`run2.json`, `run2-results.json`). Placement from the store (`run2-store.json`, `run2-remote-requests.txt`):

| Slot | Replications (config, index) | Attempt duration, s |
| --- | --- | --- |
| local-1..4 | (0,0) (0,2) (1,2) (0,3) (1,3) | 1.02 to 1.32, last 0.75 |
| strangelove-1 | (1,0) | 1.79 |
| strangelove-2 | (0,1) | 1.69 |
| otacon-1 | (1,1) | 11.02 |

`mixed_runtime` is expected here: local replications use eva's dependency set and the remote ones the provisioned environments (the msgpack record hash differs between the x86_64 and aarch64 environments as well). Environment evidence on the hosts: CPython 3.14.0, simpy 4.1.2, msgpack 1.2.3, tabulate 0.10.0, tqdm 4.70.1 (`env-listing-*.txt`).

Sizes (`snapshots/*/` in the store): source archive 40 960 bytes (the example is 6 files; tar block padding), inputs archive 10 240 bytes, frozen trusted runtime archive 4 485 120 bytes. Remote cache after the run: 8.6 MB on strangelove and 8.8 MB on otacon (`runtime` 6.2 MB, `envs` 2.4 to 2.5 MB, `blobs` 96 to 164 KB).

Timing. The store records only attempt created and updated times, not separate staging and provisioning spans, so staging is inferred from attempt durations (`run3-store.json`, a follow-up run `f0cf88b6…` with new seeds, remote workers only, 12 replications, caches warm):

| Host | First attempt of a slot, s | Later attempts, s | Cold first attempt (env + blobs built), s |
| --- | --- | --- | --- |
| strangelove (2 slots) | 1.56 / 1.47 | 0.68 to 0.78 | 1.79 / 1.69 (run 2); 1.58 / 1.69 after `workers prune` (run 4) |
| otacon (1 slot) | 4.19 | 3.03 | 11.02 |

The first attempt of a slot includes opening the ssh session and starting the supervisor (about 0.8 s on strangelove, 1.2 s on otacon); provisioning an environment adds about 0.2 s on strangelove (uv cache warm) and about 6.8 s on otacon, which also had to download CPython 3.14.0 in that run. The bigboss refusal run (2d) shows the runtime staging cost on a host that has no runtime yet: 1.0 s for the first attempt against 0.5 s for the second (4.5 MB over Tailscale). A true all-cold time with an empty cache on both hosts was not measured; the runtime was staged during 2a and `prune` does not remove it.

Throughput on the follow-up run (6 replications per configuration, one replication is a 60 time-unit simulation): strangelove 10 replications in about 4.4 s from the start of the first (about 2.3 per second with 2 slots, 0.7 s per replication per slot); otacon 2 replications in 7.2 s (0.33 per second per slot, steady 3.0 s each). The run took 7.3 s because the scheduler gave otacon two of the 12 replications and waited for them. Local: 1.0 to 1.3 s per replication on 4 slots.

### 2c. Refusal path on bigboss

`experiment run --workers bigboss` (`bigboss-run.json`, 2.7 s, exit 1): both replications `failed` with code `uv_missing` and message `simulatte: uv is missing; run: simulatte workers setup bigboss --install-uv`, no retry. The runtime archive is staged on the host before the missing uv is detected, so a refused host keeps `~/.cache/simulatte/runtime` (4.5 MB; left in place as evidence). Nothing else was installed on bigboss (`which uv` still `none`).

## Step 3: replay

CLI, original host (`replay-strangelove.json`):
`simulatte replay 5fb7b86c… --json` prints `{"status":"no_compatible_worker","message":"No slot can replay on worker strangelove"}` and exits 1. `--worker strangelove` and `--worker otacon` give the same status. A local request replays correctly through the same command (`replay-local.json`: `verified: true`, 1.35 s). Cause: `command.replay` builds the coordinator with `Coordinator.from_store` and never starts remote slot actors, so `Coordinator.replay` finds no slot of kind `uv` (D4). This does not match design section 6 for the original host, where the replay should run on strangelove.

Through the API (`sp5_replay_api.py.txt` calls `_add_workers` first, then `Coordinator.replay`; `replay-api.jsonl`):

| Replay of the strangelove request on | Result | Time |
| --- | --- | --- |
| strangelove | `verified` | 1.76 s |
| otacon (forced) | `unverified`, reason `runtime_mismatch` | 4.46 s |

The otacon replay ran (rather than failing with `no_compatible_worker`) because an explicit `worker` overrides the preference; the digest and KPI scalars matched across x86_64 and aarch64 and only the resolved runtime facts (architecture) differed, which is the documented `unverified` outcome in section 6.

## Step 4: ssh killed mid-run

`fault.experiment.json`: horizon 20 000 (about 9 s per replication), 4 replications, strangelove only, 2 slots (`fault-run.json`, `fault-store.json`). The ssh client of `strangelove-2` was killed with `kill <pid>` 1.8 s into a replication (04:23:53.354 UTC). Store: that attempt became `lost` (message `Worker transport closed`) at 04:23:53.364, 10 ms later; the replication was dispatched again at 04:23:53.366 (`dispatches` 2) and succeeded; the run completed with 4/4 succeeded. No `python3.14` or `uv run` processes remained on strangelove afterwards. (An earlier `pkill`-style pattern also matches the invoking shell command line; the pid was taken from `pgrep` of the supervisor sessions instead.)

## Step 5: cancellation over SSH

`cancel.experiment.json`: horizon 100 000 (about 40 s per replication), 2 replications on strangelove. SIGINT sent to the CLI 8 s into the run from a non-shell parent (`sp5_cancel.py.txt`; a first attempt sent SIGINT from a bash background job, which ignores it, and the run just completed, `cancel-attempt0-sigint-ignored.txt`). Result: exit 130 after 0.30 s, envelope `{"status":"paused",...,"message":"Interrupted; resume with experiment resume <id>"}`. Before SIGINT the host showed 8 processes (2 `uv run`, 2 interpreters, 4 supervisor children); right after exit and 2 s later: none. Both attempts became `lost` and both replications returned to `pending` (`cancel-status.json`).

## Step 6: `workers prune strangelove`

(`prune-strangelove.txt`) Before: 8.7 MB (`attempts` 4 K, `blobs` 180 K with 27 blobs, `envs` 2.4 M, `runtime` 6.2 M). `--dry-run` listed 27 blobs, the environment directory and its lock file and removed nothing (still 8.7 MB). The real run removed the same 29 paths, leaving 6.2 MB (only `runtime/<sha>`). A run after the prune provisioned again without error (run 4, 6 replications, first attempts 1.58 and 1.69 s).

## Step 7: platform facts

(`platform-facts.txt`, `uv-sync-argv.txt`)

| Host | `uname -a` | uv | Python in the provisioned environment |
| --- | --- | --- | --- |
| eva | Darwin 27.0.0 arm64 | 0.13.0 | 3.14.1 (local runs) |
| strangelove | Linux 7.0.0-38-generic x86_64 | 0.9.5 | CPython 3.14.0 (managed) |
| otacon | Linux 6.18.50+rpt-rpi-v8 aarch64 | 0.12.16 | CPython 3.14.0 (managed, downloaded by uv) |
| bigboss | Darwin 27.0.0 arm64 | none | not provisioned |

The `uv sync` argv is not stored in the attempt rows or in the evidence. It was reconstructed with `Provisioner.argv("uv", spec)` from the stored request runtime spec: `uv sync --locked --no-install-project --no-install-workspace --no-install-package simulatte --no-install-package uv-plain-example --python 3.14.0` (for the fixture, `shopkit` and `uv-project-example` are added with `--no-install-package`). The failing environment keys and `uv sync` stderr are stored in the attempt details when provisioning fails.

## Defects found

**D1. A uv path dependency inside the project breaks provisioning on every host (blocks the specified fixture).** `Provisioner._project` extracts only `pyproject.toml` and `uv.lock` into `<cache>/envs/<hash>.build`; `uv sync --locked --no-install-package shopkit` still needs `packages/shopkit/pyproject.toml` to read the locked `directory` source. Reproduction (any host with uv): `cd examples/uv_project && SIMULATTE_WORKERS=<file with strangelove> simulatte experiment run sweep.experiment.json --workers strangelove`. Manual reproduction (`defect-pathdep-repro.txt`): in a directory containing the fixture `pyproject.toml` and `uv.lock`, `uv sync --locked --no-install-project --no-install-workspace --no-install-package simulatte --no-install-package uv-project-example --no-install-package shopkit --python 3.14.0` exits 2 with `packages/shopkit does not appear to be a Python project`; adding only `packages/shopkit/pyproject.toml` makes it succeed (installs msgpack, simpy, tabulate, tqdm). Fix direction: also extract the `pyproject.toml` of every local path/workspace package into the build directory (and verify their hashes in the spec). This is the concern the implementer raised.

**D2. An exact `.python-version` patch pin can be unavailable on a worker.** The preflight completes `X.Y` or `X.Y.Z` to the coordinator's patch version; strangelove's uv 0.9.5 has no CPython 3.14.1 to download and its system Python is 3.14.4, so `uv run --python 3.14.1` fails with `No interpreter found for Python 3.14.1` (`strangelove-python-3.14.1.txt`, `strangelove-uv-python-list-3.14.txt`). The brief asked for the pin to equal eva's exact version, so the committed fixture pins 3.14.1 and cannot run on strangelove as is. Needs a decision (accept `X.Y` pins on hosts, or document that workers need a uv that knows the version).

**D3. The bootstrap failure reason is discarded and the failure is retried as a transient loss.** `SlotActor` (`coordinator.py`, around line 935) turns any `OSError` while waiting for the supervisor hello into `WorkerLost("Worker handshake failed")`, dropping the message that carries the remote stderr. The user sees only `Worker handshake failed`, and the replication is dispatched three times before it stays `lost`. Reproduction: D2 on strangelove. Evidence: `run1-store.json` (attempts of `strangelove-1/2`).

**D4. `simulatte replay` cannot target remote workers.** `command.replay` never starts the remote slot actors (`Coordinator._add_workers`), so replaying any remote replication, with or without `--worker`, returns `no_compatible_worker` and exit 1. Reproduction: in a project with a completed remote run, `simulatte replay <request id of a strangelove replication> --json`. With the actors started (script `sp5_replay_api.py.txt`), replay on the original host verifies and the forced replay on another architecture is `unverified (runtime_mismatch)`.

Observations, not defects: the runtime is staged on a host before the missing-uv check, so a refused host keeps a 4.5 MB `runtime` directory; the store keeps no staging or provisioning timestamps; `workers check` writes a probe file in the host cache.

## Remaining gates

- Re-run the committed fixture on strangelove and otacon once D1 and D2 are resolved; the committed `uv.lock` and pin are the inputs.
- A cold-cache measurement on an empty host (runtime, source, environment, Python download), the 1 800 s otacon provisioning timeout under load, and a mid-provisioning cancel were not exercised: provisioning here takes seconds.
- Remote replay from the CLI after D4.
- Experiment run with `full` level on a remote host and the Studio surfaces were out of scope for this task.
- Not tested: password or agent prompts (BatchMode), Windows hosts (out of scope), teban (excluded by instruction).
