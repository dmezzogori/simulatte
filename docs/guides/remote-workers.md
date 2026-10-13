# Remote workers

An experiment can run some or all of its replications on other machines. Workers are POSIX hosts that you can reach with the system `ssh` client, using your own SSH configuration. Simulatte ships the project to the host, builds a cached Python environment from the project's `uv.lock`, and runs each replication there in a fresh process. This is available from 0.17 and is unstable like the rest of the API. The experiment file and statistics are described in [Experiments and comparisons](experiments.md).

Credentials never travel. Hosts, users, ports, keys and `ControlMaster` come from `~/.ssh/config`. `ssh` is started with only a minimal environment (`PATH`, `HOME`, `USER`, the SSH agent variables, `TERM`, `LANG` and `LC_*`), and the remote command receives nothing but its bootstrap text, so values such as `UV_INDEX*` or tokens in your shell are never read or forwarded. uv is installed on a host only by `simulatte workers setup NAME --install-uv`, after an explicit confirmation.

## Configure workers

Workers are personal, not project facts, so they live in a user-level file, `~/.config/simulatte/workers.toml`. Set `SIMULATTE_WORKERS` to use another path. A missing file means no remote workers.

```toml
[workers.strangelove]
host = "strangelove"                  # ssh destination; user, port and keys come from ~/.ssh/config
slots = 4                             # concurrent replications on this host (default 1)
ssh_args = ["-o", "ServerAliveInterval=15"]   # optional, appended after the defaults
uv = "~/.local/bin/uv"                # optional explicit path to uv on the host
cache = "~/.cache/simulatte"          # optional remote cache root (this is the default)
provision_timeout = 600               # optional, seconds (this is the default)
```

| Key | Meaning |
| --- | --- |
| `host` | Required. The destination passed to `ssh`. It must not start with `-` or contain whitespace. |
| `slots` | Integer, at least 1. One SSH session and one replication at a time per slot. |
| `ssh_args` | List of strings added after the defaults and before the host. |
| `uv` | Path to uv on the host, tried before `uv` on `PATH`, `~/.local/bin/uv` and `~/.cargo/bin/uv`. |
| `cache` | Remote cache root. `~` expands on the host. |
| `provision_timeout` | Positive number of seconds for one environment build (see [Timeouts and failures](#timeouts-and-failures)). |

Worker names use letters, digits, `_`, `.` and `-`. The names `local` and `interactive`, and anything starting with `local-`, are reserved. The file is validated strictly and an unknown key is an error. Passwords, passphrases, tokens, secrets and key paths are rejected with this message (the path and worker name vary):

```text
/home/you/.config/simulatte/workers.toml: workers.lab.password: credentials are not accepted in workers.toml; use ~/.ssh/config
```

A `host` of the form `user:secret@host` is rejected the same way. Name workers in an experiment file's `workers` list, or with `--workers` on the command line; an unknown name fails before anything is scheduled. `local` stands for the local slots.

Simulatte starts `ssh` with `-o BatchMode=yes -o ConnectTimeout=10 -o ClearAllForwardings=yes` and no terminal, so a host that needs a password or a passphrase prompt cannot be used; load your key into an agent. For long runs, set `ServerAliveInterval` and `ControlMaster` in your SSH configuration. A lost connection is detected after about 5 s without heartbeats, the attempt is marked `lost`, and it is dispatched again (see [Experiments](experiments.md#resuming-cancelling-and-failures)).

## Check and set up a host

```bash
simulatte workers list                      # names, hosts and slots; no connection
simulatte workers check NAME [--json]       # connectivity, platform, shell, uv and cache; installs nothing
simulatte workers setup NAME [--install-uv] [--json]
simulatte workers prune NAME [--dry-run] [--json]
```

`check` makes one SSH call and reports the platform, the login shell, the uv version and whether the cache directory is writable. Its writability probe creates the cache directory on the host if it does not exist. The exit code is 0 when the host is reachable, has uv and a writable cache, 1 when uv or the cache is missing, and 2 for an unknown worker or an unreachable host. A Windows host is reported as unsupported.

`setup` runs the same check. If uv is missing and `--install-uv` was given, it prints the exact command, `Would run on <host>: curl -LsSf https://astral.sh/uv/install.sh | sh`, then asks `Install uv on <host> with the official installer? [y/N]`. Any answer except `y` or `yes` installs nothing, and so does a closed standard input. With `--json`, the question goes to standard error so that standard output stays one JSON document. Nothing else is ever installed.

Without uv on a host, an experiment run fails fast for that host: the replications are `failed` with code `uv_missing` and the message `simulatte: uv is missing; run: simulatte workers setup NAME --install-uv`, and they are not retried.

## What a project needs

Remote runs need the project to be a uv project. A local check, **preflight**, runs before anything is scheduled. It reads `pyproject.toml`, `uv.lock` and `.python-version` as data, runs no uv command, and does not need uv on the coordinator.

- `pyproject.toml` and `uv.lock` are in the project root, and `uv.lock` is part of the captured source (Git-tracked or matched by `[tool.simulatte.bundle] include`). Otherwise the error code is `missing_lock`.
- The lock contains `simulatte`, pinned at the **coordinator's major.minor** version. The host runs the coordinator's own Simulatte code, shipped with the bundle, but the model's other dependencies (simpy, msgpack, tabulate and tqdm among them) come from the host's environment, which must match. A lock without `simulatte` gives `missing_simulatte_dependency`; one at another major.minor gives `incompatible_simulatte`.
- Path, directory, editable and virtual dependencies in the lock must resolve **inside the project root**. They are bundled as local packages and are not installed from the lock; one outside the root is `path_dependency_outside_root`.
- Packages from an index other than PyPI produce a **warning**, `private_index:<url>`, printed to standard error by `experiment run`. The host needs its own credentials for that index in its own uv configuration. Simulatte neither reads nor forwards your local ones.
- The Python request is the project's `.python-version` as written (`3.12` or `3.12.4`), or, with no such file, the coordinator's major.minor, for example `3.14`. It must admit a version satisfying `requires-python` (`python_pin_outside_requires`); a form that Simulatte cannot evaluate gives the warning `requires_python_unparsed`. A bare `3.14` allows compatible patches, including when the project requires `>=3.14.1`; an exact `3.14.0` pin is rejected for that requirement. Each host's uv resolves the available interpreter and enforces the project requirement during provisioning. An exact patch version fails on a host whose uv does not offer it. The same request is used for the supervisor and the environment. The resolved version is recorded with each result, so hosts that differ in patch version are flagged `mixed_runtime` in the report.
- The bundle stays within 64 MiB (`bundle_too_large`). The cap is fixed in 0.17.

Preflight codes: `missing_lock`, `missing_simulatte_dependency`, `incompatible_simulatte`, `path_dependency_outside_root`, `python_pin_outside_requires`, `invalid_python_pin` (a `.python-version` that is not `X.Y` or `X.Y.Z`) and `bundle_too_large`. They appear as `validation_failed` with the code in the JSON envelope, and exit status 2.

A project that satisfies this is one that already works with `uv sync --locked`. The repository's `examples/uv_project/` is a small example with an in-root path dependency and a `.python-version` of `3.14`. Its committed lock was created against 0.16.0, so run `uv lock` in a copy of it to pin a `simulatte` whose major.minor matches your coordinator before using it. Change the copy's `simulatte==0.16.0` requirement in `pyproject.toml` first, because `uv lock` keeps an exact pin; with the committed lock as it is, preflight on a 0.17 coordinator reports `incompatible_simulatte`.

## What is shipped

The bundle for a remote run consists of:

- the source archive: Git-tracked files plus `[tool.simulatte.bundle] include`, minus `exclude`, together with `uv.lock` and the configured layout file;
- the archive of declared data inputs;
- each layout variant used by the experiment, and the request documents;
- the coordinator's frozen trusted runtime (the Simulatte package with a pure-Python copy of msgpack for the host's supervisor), about 4.5 MB.

Everything is staged by SHA-256 over the SSH channel and verified before it is published on the host. A blob a host already holds is not sent again, so the second replication ships little more than its request. In the acceptance example the source archive was 40,960 bytes, the inputs archive 10,240 bytes and the runtime archive 4,485,120 bytes. The size check applies to the source, non-source members (experiment and layout files) and the layout.

On the host, `uv sync --locked --no-install-project --no-install-workspace --no-install-package simulatte --no-install-package <each local package> --python <pin>` builds an environment of external dependencies only, with `--extra` and `--group` added when the specification names them. Local packages are loaded from the shipped, read-only source snapshot, not from the environment. The environment is cached by a hash of the specification (Python pin, lock and `pyproject.toml` hashes, extras, groups and local packages), made read-only once complete, and never changed afterwards. A failed build leaves nothing published. The host's own uv configuration, including its indexes and credentials, applies.

## The remote cache

```text
<cache>/
  runtime/<hash>/      frozen trusted runtime (one per Simulatte build)
  blobs/<sha256>       staged archives and documents, read-only
  envs/<spec-hash>/    published environments, read-only (and <spec-hash>.lock, .build while building)
  attempts/<attempt>/  per-attempt snapshot and references; removed when the attempt ends
```

`simulatte workers prune NAME` removes the blobs and environments that no running attempt references; `--dry-run` lists them first. It never removes `runtime/`. Run it when no experiment is using that worker: it uses no lock, so a blob staged a moment before its attempt directory exists could be removed. Deleting the whole cache directory is also safe; the next run stages everything again.

In the acceptance run, a prune on strangelove removed 27 blobs, one environment and its lock file (the dry run listed the same 29 paths and removed nothing), taking the cache from 8.7 MB to 6.2 MB. A later run provisioned again without error.

## Timeouts and failures

Provisioning has its own bound, `provision_timeout` (600 s by default), and is also limited by the replication's deadline when `--timeout` is set. Heartbeats continue during `uv sync`. At expiry the uv process group is killed and the replication fails with `provisioning_failed` (reason `timeout`). That is a failure, not a loss: it is not dispatched again. A transport loss during provisioning is `lost` as usual.

| Code on the replication | Meaning |
| --- | --- |
| `uv_missing` | uv was not found on the host. Not retried. |
| `runtime_missing` | The staged runtime could not be found. Not retried. |
| `bootstrap_failed` | The remote bootstrap exited with an error, for example an unavailable Python. The tail of the host's standard error (up to 4 KiB) is kept with the attempt. Not retried. |
| `provisioning_failed` | `uv sync` failed or timed out; uv's error tail is kept. Not retried. |
| `runtime_mismatch`, `runtime_changed` | The host's resolved runtime differs from the request. `failed`. |
| lost | The SSH session or host died. Dispatched again at most twice. |

## Measured behaviour

These figures come from a single remote acceptance run on 2026-10-13, not from a benchmark. The coordinator was eva (macOS arm64, CPython 3.14.1, uv 0.13.0); workers were strangelove (Linux x86_64, uv 0.9.5) and otacon (Linux aarch64, uv 0.12.16); bigboss (macOS arm64, no uv) exercised the refusal path. The details are in `specs/research/2026-10-13-sp5-remote-acceptance.md`.

- The committed fixture `examples/uv_project/` (8 replications over two configurations) ran on local, strangelove and otacon: 8 of 8 succeeded in 6.0 s on cold caches, with `mixed_runtime` reported (strangelove resolved CPython 3.14.0, otacon 3.14.1, from the bare `3.14` pin).
- First attempt of a slot, cold: 2.36 s and 2.32 s on strangelove (2 slots) and 5.99 s on otacon (1 slot). With warm caches: 1.67 s on strangelove and 4.27 s on otacon. Later attempts: 0.70 to 0.84 s on strangelove and 3.09 s on otacon. Cold minus warm is the staging and provisioning cost of the first attempt: about 0.65 s on strangelove and 1.7 s on otacon.
- Warm throughput: about 2.3 replications per second on strangelove with 2 slots, and about 0.33 per second per slot on otacon (a Raspberry Pi class host). These were short 60-time-unit simulations, so the figures describe overhead.
- With an SSH client killed in the middle of a replication, the attempt was marked `lost` within 10 ms, dispatched again, and the run completed 4 of 4 with no processes left on the host. Ctrl-C on the coordinator gave exit 130 in 0.30 s, a `paused` run, and no remaining processes on the host.
- bigboss (no uv): both replications failed immediately with `uv_missing`, and uv was not installed. The trusted runtime is staged before the uv check, so a refused host keeps a 4.5 MB `runtime` directory.

Not measured: an all-cold empty host, the 600 s provisioning timeout under load, a cancel in the middle of provisioning, and `full` level on a remote host. Password and agent prompts are untested (the SSH session is non-interactive).

## Limits

- Windows is never a remote host. A Windows coordinator keeps local experiments, but SSH workers from Windows are untested in 0.17, and `simulatte workers check` and `setup` print "SSH workers from a Windows coordinator are untested in 0.17" there.
- A PyPy coordinator can drive remote workers. The Python request is the language version (`3.11`) as a uv request, which selects CPython, so remote replications run on CPython and a configuration split across local and remote slots is flagged `mixed_runtime`.
- `simulatte replay REQUEST_ID --worker local` for a replication that ran remotely fails with "Installed runtime changed after preparation" instead of `no_compatible_worker`. Replay it without `--worker` or with the original worker.
- The login shell on the host must be POSIX-like (`sh`, `bash`, `zsh` or `fish`); `csh` and `tcsh` are not supported.
- uv path dependencies outside the project root, remote hosts on Windows and automatic worker discovery are not supported.
