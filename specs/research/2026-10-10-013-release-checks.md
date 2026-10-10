# 0.13 release checks (#56)

Date: 2026-10-10. Reviewed main commit `a22361d48deaf5b437803b316868518c7034f56c`, which merged
[PR #58](https://github.com/dmezzogori/simulatte/pull/58). This report supplements the historical
[SP1 final report](sp1-final-report.md) and [hardening report](2026-10-10-pre-013-hardening.md).

## Checklist and disposition

- Browser checks passed on the deployed 0.13.0 documentation, including plotting and the MessagePack dependency.
- Version `0.13.0` and release date `2026-10-10` were already set by #58 in `pyproject.toml`, `uv.lock`,
  `CITATION.cff` and `CHANGELOG.md`; no second bump is needed.
- The benchmark review below distinguishes the required `none` gates from report-only performance targets.
- Davide explicitly accepted the PyPy 8.0.0 recording overhead as a documented 0.13 limitation with a
  follow-up on 2026-10-10. [Issue #59](https://github.com/dmezzogori/simulatte/issues/59) tracks investigation
  and resolution. This is a release disposition, not a claim that the recording targets pass. No budget,
  noise band, runtime pin or library behavior is changed by this release-check branch.

Release tagging remains a maintainer action. This work creates no tag and publishes no package.

## Deployed documentation

[Docs run 38054549228](https://github.com/dmezzogori/simulatte/actions/runs/38054549228) deployed the reviewed
main commit. The site's `assets/wheels/latest.json` selects `simulatte-0.13.0-py3-none-any.whl`.
Chrome checks used the actual Run buttons on [the homepage](https://simulatte.dev/) and
[the intralogistics examples](https://simulatte.dev/examples/intralogistics/):

| Block | Observed output |
|---|---|
| Homepage | Makespan 5.0; utilization 100.0% |
| Simple intralogistics | 4/4 completed; warehouse inventories 69 and 31 |
| Intermediate intralogistics | 8 completed, 0 failed; two plots rendered |
| Advanced intralogistics | 65 completed, 0 failed; four plots rendered |

All six images loaded and there were no error panels. The intermediate and advanced runs reused the same
page worker as the simple example. `node scripts/docs/smoke-runnable.mjs` also passed all **19/19** runnable
blocks against the 0.13.0 docs wheel. Its initialization explicitly reported `Loading msgpack, tqdm` and
`Loaded msgpack, tqdm`; MessagePack is installed through the wheel's runtime dependency metadata.

## Main CI benchmark review

All CPython 3.11–3.15, PyPy and TypeScript test jobs passed.

Source: [main run 38054549115](https://github.com/dmezzogori/simulatte/actions/runs/38054549115), artifacts
`bench-3.14` and `bench-pypy-3.11`. Production measurements use three fresh processes, five timed repetitions
per process and one CPython or eight PyPy warm-ups. Both versions use the same dependency versions. Compare
only matching workload hashes, counts and trajectories. Intralogistics uses ten shifts, three repetitions,
and one CPython or two PyPy warm-ups.

Both benchmark jobs succeeded. All reported comparisons retain matching workload hashes, counts and trajectories.

| Runtime | Interpreter build | Runner CPU | vCPUs |
|---|---|---|---:|
| 3.14 | 3.14.8 (`main`) | AMD EPYC 9V45 96-Core Processor | 4 |
| pypy-3.11 | 3.11.16 (`78565394c9b5`) | AMD EPYC 7763 64-Core Processor | 4 |

| Runtime | Workload | Baseline | `none` overhead | Limit | Margin |
|---|---|---|---:|---:|---:|
| 3.14 | u90-5k | Released 0.12.0 | -25.9810% | 5% | 30.9810 pp |
| 3.14 | u90-5k | Stripped 0.12.0 | +6.3493% | 8.5% | 2.1507 pp |
| 3.14 | u95-5k | Released 0.12.0 | -24.0100% | 5% | 29.0100 pp |
| 3.14 | u95-5k | Stripped 0.12.0 | +8.1688% | 8.5% | 0.3312 pp |
| pypy-3.11 | u90-5k | Released 0.12.0 | -31.5917% | 10% | 41.5917 pp |
| pypy-3.11 | u90-5k | Stripped 0.12.0 | -11.4726% | 10% | 21.4726 pp |
| pypy-3.11 | u95-5k | Released 0.12.0 | -29.3398% | 10% | 39.3398 pp |
| pypy-3.11 | u95-5k | Stripped 0.12.0 | -11.4681% | 10% | 21.4681 pp |

Production report-only ratios on `jobshop10-u90-5k`:

| Runtime | Mode / baseline | Ratio | Accepted target | Assessment |
|---|---|---:|---:|---|
| 3.14 | `default` / `none` | 1.0669× | 1.1× | Within target |
| 3.14 | `default_logging` / `bare` | 1.0079× | 1.05× | Within target |
| 3.14 | `kpi` / `none` | 3.3498× | 3.7× | Within target |
| 3.14 | `digest` / `none` | 2.9907× | 3.2× | Within target |
| 3.14 | `full` / `none` | 4.2576× | 5.3× | Within target |
| pypy-3.11 | `default` / `none` | 1.0478× | 1.06× | Within target |
| pypy-3.11 | `default_logging` / `bare` | 0.9900× | 1.1× | Within target |
| pypy-3.11 | `kpi` / `none` | 8.3124× | 4.5× | Above target |
| pypy-3.11 | `digest` / `none` | 7.8214× | 4.3× | Above target |
| pypy-3.11 | `full` / `none` | 9.8135× | 7× | Above target |

Cross-version production report-only overheads on the same workload:

| Runtime | Mode | Versus released 0.12.0 | Versus stripped 0.12.0 |
|---|---|---:|---:|
| 3.14 | `default` | -22.561% | +9.009% |
| 3.14 | `default_logging` | -25.548% | +7.264% |
| pypy-3.11 | `default` | -30.658% | -13.431% |
| pypy-3.11 | `default_logging` | -32.442% | -10.964% |

Intralogistics report-only ratios (no adopted budget):

| Mode / baseline | CPython 3.14 | PyPy 3.11.16 |
|---|---:|---:|
| `default` / branch `none` | 1.0868× | 1.0565× |
| `default_logging` / branch `none` | 1.0020× | 1.0244× |
| `bare` / branch `none` | 1.0010× | 1.0122× |
| `kpi` / branch `none` | 2.3132× | 4.6414× |
| `digest` / branch `none` | 2.1515× | 4.4684× |
| `full` / branch `none` | 2.9003× | 5.3633× |
| `none` / released `none` | 1.3402× | 1.1359× |
| `default` / released `default` | 1.4561× | 1.1638× |
| `default_logging` / released `default_logging` | 1.3501× | 1.1414× |

Additional report-only rows:

| Measurement | CPython 3.14 | PyPy 3.11.16 |
|---|---:|---:|
| INFO file logging / sinks off | 1.0703× | 1.0974× |
| Sampling / 0.12.0 | 0.7681× | 0.5222× |

The `none` limits remain 5% versus released / 8.5% versus stripped on CPython, and 10% versus either baseline
on PyPy. The latter is the existing 3% budget plus the runner-calibrated 7% noise band from #55; the benchmark
README's stale 5% band has been corrected to match CI.

The recording target failure was already present before #58. On
[previous main run 38051094090](https://github.com/dmezzogori/simulatte/actions/runs/38051094090), PyPy
`digest` / `kpi` / `full` ratios were 7.961× / 8.436× / 9.818×. On the tree merged by #58,
[PR run 38051993548](https://github.com/dmezzogori/simulatte/actions/runs/38051993548) gave
7.750× / 8.779× / 9.911×. These rows are report-only and therefore do not fail CI.

Intralogistics has no adopted performance budget. Its cross-version rows compare the compatibility shim's
matched input requests and normalized lifecycle histories; a runtime increase is reported rather than
silently treated as a gate failure or a guarantee of performance parity. INFO logging is also reported
separately: the accepted `default_logging` bookkeeping target applies to a workload without INFO messages,
not to the one-file-log-per-job measurement.

## PyPy diagnostic evidence for #59

A source archive of `a22361d` was run on strangelove (Ubuntu 24.04, Ryzen 7 3800X) in isolated environments
with SimPy 4.1.2 and msgpack 1.2.3. The same `jobshop10-u90-5k.json` workload used eight warm-ups and five
measurements per process. These are exploratory single-process probes, not new CI calibration. The host was
not reserved exclusively, and a short hashing probe overlapped part of the stock 8.0.0 measurements.

| Runtime | `none` median | `digest` median | Ratio |
|---|---:|---:|---:|
| PyPy 7.3.20 / Python 3.11.13 | 0.3467 s | 1.4863 s | 4.287× |
| Stock PyPy 8.0.0 / Python 3.11.16 | 0.3240 s | 2.4863 s | 7.674× |
| PyPy 8.0.0 with only a scratch BLAKE2 extension rebuilt using `-O3` | same stock denominator | 1.9883 s | 6.137× |

Counts and trajectories match in every probe. All digest runs produced
`cf6480d5fa80efb03eb30f138035e17b88a52f2a6596f1d098b6d4b724cc4c9a`.

A separate 100,000-iteration microbenchmark on the same host isolates two operations:

| Operation | PyPy 7.3.20 | Stock PyPy 8.0.0 | 8.0.0, scratch optimized BLAKE2 |
|---|---:|---:|---:|
| MessagePack pack of the same event-shaped value | 0.0746 s | 0.0735 s | 0.0737 s |
| BLAKE2b-256 updates of its length prefix and bytes | 0.0184 s | 0.1428 s | 0.0245 s |

This implicates the bundled hashing extension in **part** of the runtime difference. It does not establish
the cause of all recording overhead. Rebuilding that extension did not restore the digest target. Its C
compression function's symbol size also differs substantially (9,419 versus 68,241 bytes in the stock old
and new binaries), but that alone does not establish the compiler flags used upstream.
The [PyPy 8.0 release notes](https://doc.pypy.org/release-v8.0.0.html) document a Linux toolchain/build-image
change; attributing the measured regression to a particular upstream change needs further investigation.
No supported interpreter installation was modified, and Simulatte ships no interpreter patch.

Reproduce the microbenchmark with each interpreter (msgpack 1.2.3 installed):

```python
import gc
import hashlib
import statistics
import struct
import time

import msgpack

item = (
    1,
    "job.started",
    1,
    123.5,
    {"job": "job-1", "server": "wc-1"},
    [("set", "job-1", "op_index", 2), ("add", "wc-1", "users", "job-1")],
)
packer = msgpack.Packer(use_bin_type=True)
packed = packer.pack(item)
size = struct.pack(">Q", len(packed))


def run(mode):
    digest = hashlib.blake2b(digest_size=32)
    for _ in range(100_000):
        if mode == "pack":
            packer.pack(item)
        else:
            digest.update(size)
            digest.update(packed)
    return digest.hexdigest()


for mode in ("pack", "hash"):
    samples = []
    for _ in range(8):
        gc.collect()
        start = time.perf_counter()
        run(mode)
        samples.append(time.perf_counter() - start)
    print(mode, statistics.median(samples[3:]))
```

End-to-end reproduction uses the unchanged benchmark driver:

```sh
python benchmarks/run.py --mode none --workload benchmarks/workloads/jobshop10-u90-5k.json \
  --warmup 8 --repeat 5 --no-memory --json /tmp/none.json
python benchmarks/run.py --mode digest --workload benchmarks/workloads/jobshop10-u90-5k.json \
  --warmup 8 --repeat 5 --no-memory --json /tmp/digest.json
python benchmarks/compare.py /tmp/none.json /tmp/digest.json
```

Temporary local results were saved as `/tmp/issue56-linux{,-ci,-opt}-*.json`; final main-run CI artifacts are
the durable source for release decisions. Follow-up #59 should retain new measurements, isolate the remaining
cost, prepare an upstream reproducer if appropriate, and verify all modes and larger workloads before
removing the limitation. No upstream issue has been submitted by this release-check work.

## Verification of the documentation change

`uv run --all-extras zensical build` succeeded, and
`uv run --all-extras python scripts/check_docs_links.py` resolved all internal links across 51 pages.
The generated PyPy page includes the limitation, follow-up link and the anchor referenced by the changelog.
The Python microbenchmark above was extracted from this report and executed successfully on PyPy.
There are no library, test or workflow changes in this branch.
