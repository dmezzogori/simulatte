# Exact benchmark command ledger

Every setup, measurement, comparison and report invocation has an individual exit status. Orchestration elapsed time includes process startup and any documented gap; it is not a benchmark median.

## metadata

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.351s. Start: 2026-10-10T22:40:35+0200. End: 2026-10-10T22:40:35+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/metadata.log`.

```sh
sh -c 'hostname; sw_vers; sysctl -n machdep.cpu.brand_string hw.ncpu hw.memsize; uv --version; node --version; pnpm --version; git rev-parse HEAD; git status --short; ps -axo pid,ppid,%cpu,command'
```

## cpython/setup-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.014s. Start: 2026-10-10T22:40:35+0200. End: 2026-10-10T22:40:36+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/setup-head.log`.

```sh
uv venv /tmp/simulatte-sp2-bench.jsKjEr/cpython/head --python 3.14
```

## cpython/install-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 1.121s. Start: 2026-10-10T22:40:36+0200. End: 2026-10-10T22:40:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/install-head.log`.

```sh
uv pip install --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/head '.[all]'
```

## cpython/freeze-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.012s. Start: 2026-10-10T22:40:37+0200. End: 2026-10-10T22:40:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/freeze-head.log`.

```sh
uv pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/head
```

## cpython/setup-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:40:37+0200. End: 2026-10-10T22:40:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/setup-base.log`.

```sh
uv venv /tmp/simulatte-sp2-bench.jsKjEr/cpython/base --python 3.14
```

## cpython/install-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **2**. Elapsed: 0.010s. Start: 2026-10-10T22:40:37+0200. End: 2026-10-10T22:40:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/install-base.log`.

```sh
uv pip install --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/base simulatte==0.12.0 --constraint /tmp/simulatte-sp2-bench.jsKjEr/cpython/constraints.txt
```

## cpython/freeze-head-quiet

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:41:02+0200. End: 2026-10-10T22:41:02+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/freeze-head-quiet.log`.

```sh
uv -q pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/head
```

## cpython/install-base-corrected

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.153s. Start: 2026-10-10T22:41:02+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/install-base-corrected.log`.

```sh
uv pip install --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/base simulatte==0.12.0 --constraint /tmp/simulatte-sp2-bench.jsKjEr/cpython/constraints.txt
```

## cpython/freeze-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.010s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/freeze-base.log`.

```sh
uv pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/cpython/base
```

## cpython/strip

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.059s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/strip.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python benchmarks/strip_debug.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/lib/python3.14/site-packages/simulatte /tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped
```

## cpython/verify-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.055s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/verify-base.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/cpython/base'"'"'+"/")'
```

## cpython/verify-stripped

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.051s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/verify-stripped.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped'"'"'+"/")'
```

## cpython/verify-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.286s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/verify-head.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/cpython/head'"'"'+"/")'
```

## pypy/setup-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/setup-head.log`.

```sh
uv venv /tmp/simulatte-sp2-bench.jsKjEr/pypy/head --python pypy-3.11
```

## pypy/install-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 1.401s. Start: 2026-10-10T22:41:03+0200. End: 2026-10-10T22:41:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/install-head.log`.

```sh
uv pip install --python /tmp/simulatte-sp2-bench.jsKjEr/pypy/head '.[all]'
```

## pypy/freeze-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:41:04+0200. End: 2026-10-10T22:41:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/freeze-head.log`.

```sh
uv pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/pypy/head
```

## pypy/setup-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:41:04+0200. End: 2026-10-10T22:41:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/setup-base.log`.

```sh
uv venv /tmp/simulatte-sp2-bench.jsKjEr/pypy/base --python pypy-3.11
```

## pypy/freeze-head-quiet

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.018s. Start: 2026-10-10T22:41:04+0200. End: 2026-10-10T22:41:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/freeze-head-quiet.log`.

```sh
uv -q pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/pypy/head
```

## pypy/install-base-corrected

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.058s. Start: 2026-10-10T22:41:04+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/install-base-corrected.log`.

```sh
uv pip install --python /tmp/simulatte-sp2-bench.jsKjEr/pypy/base simulatte==0.12.0 --constraint /tmp/simulatte-sp2-bench.jsKjEr/pypy/constraints.txt
```

## pypy/freeze-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.011s. Start: 2026-10-10T22:41:05+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/freeze-base.log`.

```sh
uv pip freeze --python /tmp/simulatte-sp2-bench.jsKjEr/pypy/base
```

## pypy/strip

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.122s. Start: 2026-10-10T22:41:05+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/strip.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python benchmarks/strip_debug.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/lib/pypy3.11/site-packages/simulatte /tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped
```

## pypy/verify-base

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.092s. Start: 2026-10-10T22:41:05+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/verify-base.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/pypy/base'"'"'+"/")'
```

## pypy/verify-stripped

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.085s. Start: 2026-10-10T22:41:05+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/verify-stripped.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped'"'"'+"/")'
```

## pypy/verify-head

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.206s. Start: 2026-10-10T22:41:05+0200. End: 2026-10-10T22:41:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/verify-head.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python -c 'import sys, platform, simulatte, importlib.metadata; print(sys.version); print(platform.platform()); print(sys.executable); print(simulatte.__file__); print(importlib.metadata.version("simulatte")); assert simulatte.__file__.startswith('"'"'/tmp/simulatte-sp2-bench.jsKjEr/pypy/head'"'"'+"/")'
```

## cpython/processes-before

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.033s. Start: 2026-10-10T22:41:35+0200. End: 2026-10-10T22:41:35+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/processes-before.log`.

```sh
ps -axo pid,ppid,%cpu,command
```

## cpython/base-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 17.296s. Start: 2026-10-10T22:41:35+0200. End: 2026-10-10T22:41:52+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/base-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u90-5k.json
```

## cpython/stripped-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.134s. Start: 2026-10-10T22:41:52+0200. End: 2026-10-10T22:42:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped-none-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## cpython/head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.754s. Start: 2026-10-10T22:42:04+0200. End: 2026-10-10T22:42:17+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json
```

## cpython/compare-base-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:42:17+0200. End: 2026-10-10T22:42:17+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-base-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json --budget 0.03 --noise 0.02 --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-stripped-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.036s. Start: 2026-10-10T22:42:17+0200. End: 2026-10-10T22:42:17+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-stripped-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json --budget 0.065 --noise 0.02 --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/base-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 18.067s. Start: 2026-10-10T22:42:17+0200. End: 2026-10-10T22:42:35+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/base-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u95-5k.json
```

## cpython/stripped-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.871s. Start: 2026-10-10T22:42:35+0200. End: 2026-10-10T22:42:48+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped-none-jobshop10-u95-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u95-5k.json --label '0.12.0 without env.debug calls'
```

## cpython/head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 13.574s. Start: 2026-10-10T22:42:48+0200. End: 2026-10-10T22:43:01+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u95-5k.json
```

## cpython/compare-base-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:43:01+0200. End: 2026-10-10T22:43:01+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-base-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u95-5k.json --budget 0.03 --noise 0.02 --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-stripped-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:43:01+0200. End: 2026-10-10T22:43:01+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-stripped-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u95-5k.json --budget 0.065 --noise 0.02 --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/base-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 17.626s. Start: 2026-10-10T22:43:01+0200. End: 2026-10-10T22:43:19+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/base-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default-jobshop10-u90-5k.json
```

## cpython/stripped-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.436s. Start: 2026-10-10T22:43:19+0200. End: 2026-10-10T22:43:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped-default-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## cpython/head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 13.489s. Start: 2026-10-10T22:43:32+0200. End: 2026-10-10T22:43:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default-jobshop10-u90-5k.json
```

## cpython/compare-base-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:43:45+0200. End: 2026-10-10T22:43:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-base-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-stripped-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.036s. Start: 2026-10-10T22:43:45+0200. End: 2026-10-10T22:43:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-stripped-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/base-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 17.242s. Start: 2026-10-10T22:43:45+0200. End: 2026-10-10T22:44:02+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/base-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default_logging-jobshop10-u90-5k.json
```

## cpython/stripped-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.098s. Start: 2026-10-10T22:44:02+0200. End: 2026-10-10T22:44:14+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped-default_logging-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/cpython/stripped /tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default_logging-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## cpython/head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 13.007s. Start: 2026-10-10T22:44:14+0200. End: 2026-10-10T22:44:27+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default_logging-jobshop10-u90-5k.json
```

## cpython/compare-base-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:44:27+0200. End: 2026-10-10T22:44:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-base-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-stripped-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.035s. Start: 2026-10-10T22:44:28+0200. End: 2026-10-10T22:44:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-stripped-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/head-bare-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.769s. Start: 2026-10-10T22:44:28+0200. End: 2026-10-10T22:44:40+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-bare-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode bare --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-bare-jobshop10-u90-5k.json
```

## cpython/compare-head-bare-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:44:40+0200. End: 2026-10-10T22:44:40+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-head-bare-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/head-kpi-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 42.138s. Start: 2026-10-10T22:44:40+0200. End: 2026-10-10T22:45:22+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-kpi-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode kpi --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-kpi-jobshop10-u90-5k.json
```

## cpython/compare-head-none-jobshop10-u90-5k--head-kpi-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:45:22+0200. End: 2026-10-10T22:45:23+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-head-none-jobshop10-u90-5k--head-kpi-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-kpi-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/head-digest-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 77.142s. Start: 2026-10-10T22:45:23+0200. End: 2026-10-10T22:46:40+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-digest-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode digest --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-digest-jobshop10-u90-5k.json
```

## cpython/compare-head-none-jobshop10-u90-5k--head-digest-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.050s. Start: 2026-10-10T22:46:40+0200. End: 2026-10-10T22:46:40+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-head-none-jobshop10-u90-5k--head-digest-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-digest-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/head-full-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 72.570s. Start: 2026-10-10T22:46:40+0200. End: 2026-10-10T22:47:52+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-full-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode full --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-full-jobshop10-u90-5k.json
```

## cpython/compare-head-none-jobshop10-u90-5k--head-full-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.037s. Start: 2026-10-10T22:47:52+0200. End: 2026-10-10T22:47:52+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-head-none-jobshop10-u90-5k--head-full-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-full-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/head-info-bare-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.125s. Start: 2026-10-10T22:47:52+0200. End: 2026-10-10T22:48:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-info-bare-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode bare --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-bare-jobshop10-u90-5k.json --log-info --no-memory
```

## cpython/head-info-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 13.475s. Start: 2026-10-10T22:48:04+0200. End: 2026-10-10T22:48:18+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-info-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 1 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-default_logging-jobshop10-u90-5k.json --log-info --no-memory
```

## cpython/compare-head-info-bare-jobshop10-u90-5k--head-info-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:48:18+0200. End: 2026-10-10T22:48:18+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-head-info-bare-jobshop10-u90-5k--head-info-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/base-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 1.664s. Start: 2026-10-10T22:48:18+0200. End: 2026-10-10T22:48:20+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/base-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python bench_sampling.py --jobs 20000 --warmup 1 --repeat 5 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-sampling.json
```

## cpython/head-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 1.335s. Start: 2026-10-10T22:48:20+0200. End: 2026-10-10T22:48:21+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/head-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python bench_sampling.py --jobs 20000 --warmup 1 --repeat 5 --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-sampling.json
```

## cpython/compare-base-sampling--head-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.036s. Start: 2026-10-10T22:48:21+0200. End: 2026-10-10T22:48:21+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-base-sampling--head-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-sampling.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/il-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 11.512s. Start: 2026-10-10T22:48:21+0200. End: 2026-10-10T22:48:33+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode none --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json
```

## cpython/il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 12.643s. Start: 2026-10-10T22:48:33+0200. End: 2026-10-10T22:48:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode default --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default.json
```

## cpython/il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 11.737s. Start: 2026-10-10T22:48:45+0200. End: 2026-10-10T22:48:57+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode default_logging --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default_logging.json
```

## cpython/il-bare

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 11.469s. Start: 2026-10-10T22:48:57+0200. End: 2026-10-10T22:49:08+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-bare.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode bare --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-bare.json
```

## cpython/il-kpi

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 26.542s. Start: 2026-10-10T22:49:08+0200. End: 2026-10-10T22:49:35+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-kpi.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode kpi --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-kpi.json
```

## cpython/il-digest

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 24.316s. Start: 2026-10-10T22:49:35+0200. End: 2026-10-10T22:49:59+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-digest.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode digest --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-digest.json
```

## cpython/il-full

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 33.091s. Start: 2026-10-10T22:49:59+0200. End: 2026-10-10T22:50:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-full.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python intralogistics.py --mode full --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-full.json
```

## cpython/compare-il-none--il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:50:32+0200. End: 2026-10-10T22:50:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-il-none--il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:50:32+0200. End: 2026-10-10T22:50:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default_logging.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-il-none--il-bare

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.048s. Start: 2026-10-10T22:50:32+0200. End: 2026-10-10T22:50:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-bare.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-bare.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-il-none--il-kpi

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.039s. Start: 2026-10-10T22:50:32+0200. End: 2026-10-10T22:50:32+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-kpi.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-kpi.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-il-none--il-digest

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.040s. Start: 2026-10-10T22:50:32+0200. End: 2026-10-10T22:50:33+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-digest.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-digest.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/compare-il-none--il-full

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:50:33+0200. End: 2026-10-10T22:50:33+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-none--il-full.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-full.json --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/il-base-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 8.786s. Start: 2026-10-10T22:50:33+0200. End: 2026-10-10T22:50:41+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-base-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python intralogistics.py --mode none --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-none.json
```

## cpython/compare-il-base-none--il-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.039s. Start: 2026-10-10T22:50:41+0200. End: 2026-10-10T22:50:41+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-base-none--il-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/il-base-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 8.808s. Start: 2026-10-10T22:50:41+0200. End: 2026-10-10T22:50:50+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-base-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python intralogistics.py --mode default --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default.json
```

## cpython/compare-il-base-default--il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:50:50+0200. End: 2026-10-10T22:50:50+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-base-default--il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/il-base-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 8.758s. Start: 2026-10-10T22:50:50+0200. End: 2026-10-10T22:50:59+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/il-base-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/base/bin/python intralogistics.py --mode default_logging --shifts 10 --warmup 1 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default_logging.json
```

## cpython/compare-il-base-default_logging--il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.038s. Start: 2026-10-10T22:50:59+0200. End: 2026-10-10T22:50:59+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/compare-il-base-default_logging--il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default_logging.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/cpython/summary.md
```

## cpython/details

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.042s. Start: 2026-10-10T22:50:59+0200. End: 2026-10-10T22:50:59+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/details.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python summarize.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/base-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-digest-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-full-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-info-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-kpi-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/head-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-bare.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-base-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-digest.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-full.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-kpi.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/cpython/out/stripped-none-jobshop10-u95-5k.json
```

## cpython/replications

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 3.878s. Start: 2026-10-10T22:50:59+0200. End: 2026-10-10T22:51:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/replications.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python benchmarks/bench_replications.py --replications 8 --horizon 1000 --workers 2 --repeat 3
```

## cpython/processes-after

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.032s. Start: 2026-10-10T22:51:03+0200. End: 2026-10-10T22:51:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/cpython/processes-after.log`.

```sh
ps -axo pid,ppid,%cpu,command
```

## source-manifest

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.138s. Start: 2026-10-10T22:53:14+0200. End: 2026-10-10T22:53:14+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/source-manifest.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python -c 'from pathlib import Path
import hashlib,json,simulatte
repo=Path('"'"'/Users/davide/Developer/simulatte/src/simulatte'"'"'); package=Path(simulatte.__file__).parent
files=[]
for p in sorted(repo.rglob('"'"'*'"'"')):
    if not p.is_file() or '"'"'__pycache__'"'"' in p.parts:continue
    rel=p.relative_to(repo); q=package/rel
    if p.suffix not in ('"'"'.py'"'"','"'"'.json'"'"') and p.name!='"'"'py.typed'"'"':continue
    assert q.is_file(),str(rel)
    a=hashlib.sha256(p.read_bytes()).hexdigest();b=hashlib.sha256(q.read_bytes()).hexdigest()
    assert a==b,str(rel)
    files.append({'"'"'path'"'"':str(rel),'"'"'sha256'"'"':a})
print(json.dumps({'"'"'source_commit'"'"':'"'"'6a3f131d8eec99d6552ed4c5ddf5807a3beea104'"'"','"'"'installed_package'"'"':str(package),'"'"'verified_source_files'"'"':len(files),'"'"'files'"'"':files},indent=2))
'
```

## pypy/processes-before

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.034s. Start: 2026-10-10T23:00:51+0200. End: 2026-10-10T23:00:51+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/processes-before.log`.

```sh
ps -axo pid,ppid,%cpu,command
```

## pypy/base-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 28.612s. Start: 2026-10-10T23:00:51+0200. End: 2026-10-10T23:01:19+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/base-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u90-5k.json
```

## pypy/stripped-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 21.155s. Start: 2026-10-10T23:01:19+0200. End: 2026-10-10T23:01:40+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped-none-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## pypy/head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 16.559s. Start: 2026-10-10T23:01:40+0200. End: 2026-10-10T23:01:57+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode none --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json
```

## pypy/compare-base-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.055s. Start: 2026-10-10T23:01:57+0200. End: 2026-10-10T23:01:57+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-base-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json --budget 0.03 --noise 0.07 --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-stripped-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.056s. Start: 2026-10-10T23:01:57+0200. End: 2026-10-10T23:01:57+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-stripped-none-jobshop10-u90-5k--head-none-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json --budget 0.03 --noise 0.07 --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/base-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 26.314s. Start: 2026-10-10T23:01:57+0200. End: 2026-10-10T23:02:23+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/base-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u95-5k.json
```

## pypy/stripped-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 21.841s. Start: 2026-10-10T23:02:23+0200. End: 2026-10-10T23:02:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped-none-jobshop10-u95-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u95-5k.json --label '0.12.0 without env.debug calls'
```

## pypy/head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 18.084s. Start: 2026-10-10T23:02:45+0200. End: 2026-10-10T23:03:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode none --workload workloads/jobshop10-u95-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u95-5k.json
```

## pypy/compare-base-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.057s. Start: 2026-10-10T23:03:03+0200. End: 2026-10-10T23:03:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-base-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u95-5k.json --budget 0.03 --noise 0.07 --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-stripped-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.058s. Start: 2026-10-10T23:03:03+0200. End: 2026-10-10T23:03:04+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-stripped-none-jobshop10-u95-5k--head-none-jobshop10-u95-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u95-5k.json --budget 0.03 --noise 0.07 --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/base-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 25.440s. Start: 2026-10-10T23:03:04+0200. End: 2026-10-10T23:03:29+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/base-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default-jobshop10-u90-5k.json
```

## pypy/stripped-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 21.392s. Start: 2026-10-10T23:03:29+0200. End: 2026-10-10T23:03:50+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped-default-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## pypy/head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 16.324s. Start: 2026-10-10T23:03:50+0200. End: 2026-10-10T23:04:07+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode default --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default-jobshop10-u90-5k.json
```

## pypy/compare-base-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.052s. Start: 2026-10-10T23:04:07+0200. End: 2026-10-10T23:04:07+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-base-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-stripped-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.052s. Start: 2026-10-10T23:04:07+0200. End: 2026-10-10T23:04:07+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-stripped-default-jobshop10-u90-5k--head-default-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/base-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 25.952s. Start: 2026-10-10T23:04:07+0200. End: 2026-10-10T23:04:33+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/base-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default_logging-jobshop10-u90-5k.json
```

## pypy/stripped-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 20.069s. Start: 2026-10-10T23:04:33+0200. End: 2026-10-10T23:04:53+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped-default_logging-jobshop10-u90-5k.log`.

```sh
PYTHONPATH=/tmp/simulatte-sp2-bench.jsKjEr/pypy/stripped /tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default_logging-jobshop10-u90-5k.json --label '0.12.0 without env.debug calls'
```

## pypy/head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 15.754s. Start: 2026-10-10T23:04:53+0200. End: 2026-10-10T23:05:09+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default_logging-jobshop10-u90-5k.json
```

## pypy/compare-base-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.054s. Start: 2026-10-10T23:05:09+0200. End: 2026-10-10T23:05:09+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-base-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-stripped-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.053s. Start: 2026-10-10T23:05:09+0200. End: 2026-10-10T23:05:09+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-stripped-default_logging-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/head-bare-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 15.571s. Start: 2026-10-10T23:05:09+0200. End: 2026-10-10T23:05:24+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-bare-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode bare --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-bare-jobshop10-u90-5k.json
```

## pypy/compare-head-bare-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.053s. Start: 2026-10-10T23:05:24+0200. End: 2026-10-10T23:05:24+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-head-bare-jobshop10-u90-5k--head-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/head-kpi-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 58.119s. Start: 2026-10-10T23:05:24+0200. End: 2026-10-10T23:06:22+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-kpi-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode kpi --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-kpi-jobshop10-u90-5k.json
```

## pypy/compare-head-none-jobshop10-u90-5k--head-kpi-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.062s. Start: 2026-10-10T23:06:22+0200. End: 2026-10-10T23:06:22+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-head-none-jobshop10-u90-5k--head-kpi-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-kpi-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/head-digest-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 56.434s. Start: 2026-10-10T23:06:22+0200. End: 2026-10-10T23:07:19+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-digest-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode digest --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-digest-jobshop10-u90-5k.json
```

## pypy/compare-head-none-jobshop10-u90-5k--head-digest-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.054s. Start: 2026-10-10T23:07:19+0200. End: 2026-10-10T23:07:19+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-head-none-jobshop10-u90-5k--head-digest-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-digest-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/head-full-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 96.072s. Start: 2026-10-10T23:07:19+0200. End: 2026-10-10T23:08:55+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-full-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode full --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-full-jobshop10-u90-5k.json
```

## pypy/compare-head-none-jobshop10-u90-5k--head-full-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.054s. Start: 2026-10-10T23:08:55+0200. End: 2026-10-10T23:08:55+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-head-none-jobshop10-u90-5k--head-full-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-full-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/head-info-bare-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 15.702s. Start: 2026-10-10T23:08:55+0200. End: 2026-10-10T23:09:11+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-info-bare-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode bare --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-bare-jobshop10-u90-5k.json --log-info --no-memory
```

## pypy/head-info-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 17.496s. Start: 2026-10-10T23:09:11+0200. End: 2026-10-10T23:09:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-info-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python run.py --mode default_logging --workload workloads/jobshop10-u90-5k.json --warmup 8 --repeat 5 --processes 3 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-default_logging-jobshop10-u90-5k.json --log-info --no-memory
```

## pypy/compare-head-info-bare-jobshop10-u90-5k--head-info-default_logging-jobshop10-u90-5k

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.053s. Start: 2026-10-10T23:09:28+0200. End: 2026-10-10T23:09:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-head-info-bare-jobshop10-u90-5k--head-info-default_logging-jobshop10-u90-5k.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-default_logging-jobshop10-u90-5k.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/base-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 5.586s. Start: 2026-10-10T23:09:28+0200. End: 2026-10-10T23:09:34+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/base-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python bench_sampling.py --jobs 20000 --warmup 8 --repeat 5 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-sampling.json
```

## pypy/head-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 1.682s. Start: 2026-10-10T23:09:34+0200. End: 2026-10-10T23:09:36+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/head-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python bench_sampling.py --jobs 20000 --warmup 8 --repeat 5 --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-sampling.json
```

## pypy/compare-base-sampling--head-sampling

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.052s. Start: 2026-10-10T23:09:36+0200. End: 2026-10-10T23:09:36+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-base-sampling--head-sampling.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-sampling.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/il-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.063s. Start: 2026-10-10T23:09:36+0200. End: 2026-10-10T23:09:45+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode none --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json
```

## pypy/il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.211s. Start: 2026-10-10T23:09:45+0200. End: 2026-10-10T23:09:54+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode default --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default.json
```

## pypy/il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.013s. Start: 2026-10-10T23:09:54+0200. End: 2026-10-10T23:10:03+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode default_logging --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default_logging.json
```

## pypy/il-bare

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.401s. Start: 2026-10-10T23:10:03+0200. End: 2026-10-10T23:10:12+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-bare.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode bare --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-bare.json
```

## pypy/il-kpi

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 22.748s. Start: 2026-10-10T23:10:12+0200. End: 2026-10-10T23:10:35+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-kpi.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode kpi --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-kpi.json
```

## pypy/il-digest

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 21.734s. Start: 2026-10-10T23:10:35+0200. End: 2026-10-10T23:10:57+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-digest.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode digest --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-digest.json
```

## pypy/il-full

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 30.775s. Start: 2026-10-10T23:10:57+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-full.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python intralogistics.py --mode full --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-full.json
```

## pypy/compare-il-none--il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.058s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-il-none--il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.057s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default_logging.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-il-none--il-bare

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.055s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-bare.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-bare.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-il-none--il-kpi

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.075s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-kpi.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-kpi.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-il-none--il-digest

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.056s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-digest.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-digest.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/compare-il-none--il-full

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.055s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:28+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-none--il-full.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-full.json --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/il-base-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.050s. Start: 2026-10-10T23:11:28+0200. End: 2026-10-10T23:11:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-base-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python intralogistics.py --mode none --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-none.json
```

## pypy/compare-il-base-none--il-none

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.065s. Start: 2026-10-10T23:11:37+0200. End: 2026-10-10T23:11:37+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-base-none--il-none.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/il-base-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.480s. Start: 2026-10-10T23:11:37+0200. End: 2026-10-10T23:11:47+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-base-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python intralogistics.py --mode default --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default.json
```

## pypy/compare-il-base-default--il-default

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.052s. Start: 2026-10-10T23:11:47+0200. End: 2026-10-10T23:11:47+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-base-default--il-default.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/il-base-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 9.190s. Start: 2026-10-10T23:11:47+0200. End: 2026-10-10T23:11:56+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/il-base-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/base/bin/python intralogistics.py --mode default_logging --shifts 10 --warmup 2 --repeat 3 --no-memory --json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default_logging.json
```

## pypy/compare-il-base-default_logging--il-default_logging

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.052s. Start: 2026-10-10T23:11:56+0200. End: 2026-10-10T23:11:56+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/compare-il-base-default_logging--il-default_logging.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python compare.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default_logging.json --report-only --summary /tmp/simulatte-sp2-bench.jsKjEr/pypy/summary.md
```

## pypy/details

Cwd: `/Users/davide/Developer/simulatte/benchmarks`. Exit: **0**. Elapsed: 0.057s. Start: 2026-10-10T23:11:56+0200. End: 2026-10-10T23:11:56+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/details.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python summarize.py /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/base-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-digest-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-full-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-bare-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-info-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-kpi-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-none-jobshop10-u95-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/head-sampling.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-bare.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-base-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-default_logging.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-digest.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-full.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-kpi.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/il-none.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-default_logging-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u90-5k.json /tmp/simulatte-sp2-bench.jsKjEr/pypy/out/stripped-none-jobshop10-u95-5k.json
```

## pypy/replications

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 8.698s. Start: 2026-10-10T23:11:56+0200. End: 2026-10-10T23:12:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/replications.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/pypy/head/bin/python benchmarks/bench_replications.py --replications 8 --horizon 1000 --workers 2 --repeat 3
```

## pypy/processes-after

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.031s. Start: 2026-10-10T23:12:05+0200. End: 2026-10-10T23:12:05+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/pypy/processes-after.log`.

```sh
ps -axo pid,ppid,%cpu,command
```

## calibrate-noise

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.179s. Start: 2026-10-10T23:12:07+0200. End: 2026-10-10T23:12:08+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/calibrate-noise.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python benchmarks/calibrate_noise.py /tmp/simulatte-sp2-bench.jsKjEr/cpython/out /tmp/simulatte-sp2-bench.jsKjEr/pypy/out
```

## ts-benchmark

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.994s. Start: 2026-10-10T23:12:08+0200. End: 2026-10-10T23:12:09+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/ts-benchmark.log`.

```sh
pnpm --dir studio/packages/trace benchmark --iterations 30 --cursors 8
```

## artifact-audit

Cwd: `/Users/davide/Developer/simulatte`. Exit: **0**. Elapsed: 0.032s. Start: 2026-10-10T23:12:16+0200. End: 2026-10-10T23:12:16+0200. Log: `/tmp/simulatte-sp2-bench.jsKjEr/artifact-audit.log`.

```sh
/tmp/simulatte-sp2-bench.jsKjEr/cpython/head/bin/python -c 'from pathlib import Path
import json
r=Path('"'"'/tmp/simulatte-sp2-bench.jsKjEr'"'"')
for lane in ['"'"'cpython'"'"','"'"'pypy'"'"']:
    d=r/lane/'"'"'out'"'"'; rows=[json.loads(p.read_text()) for p in d.glob('"'"'*.json'"'"')]
    print(lane, len(rows), '"'"'measurement artifacts'"'"')
    assert len(rows)==30
    assert all(x['"'"'commit'"'"']=='"'"'6a3f131d8eec99d6552ed4c5ddf5807a3beea104'"'"' for x in rows)
    def freeze(side):return {s.split('"'"'=='"'"')[0]:s.split('"'"'=='"'"')[1] for s in (r/lane/f'"'"'freeze-{side}.log'"'"').read_text().splitlines() if '"'"'=='"'"' in s}
    b,h=freeze('"'"'base'"'"'),freeze('"'"'head'"'"');shared=set(b)&set(h)-{'"'"'simulatte'"'"'}
    assert all(b[k]==h[k] for k in shared)
    print('"'"'matching shared dependency pins'"'"', {k:b[k] for k in sorted(shared)})
    for w in ['"'"'jobshop10-u90-5k'"'"','"'"'jobshop10-u95-5k'"'"']:
        for side in ['"'"'base'"'"','"'"'stripped'"'"','"'"'head'"'"']:
            row=json.loads((d/f'"'"'{side}-none-{w}.json'"'"').read_text())
            assert row['"'"'repeat'"'"']==5 and row['"'"'processes'"'"']==3 and row['"'"'warmup'"'"']==(8 if lane=='"'"'pypy'"'"' else 1)
print('"'"'All60 measurements retain source identity; environment constraints and exact gate sample configuration verified.'"'"')
'
```
