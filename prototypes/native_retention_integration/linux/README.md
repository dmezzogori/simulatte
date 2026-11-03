# Linux integration validation

This is the CPython 3.14 Linux x86-64 counterpart of the installed macOS gate.
Production sources, workloads, native API and existing research evidence stay
unchanged. The ordinary `run` and `analysis` modules are reused without edits;
the Linux-specific safety/check and cross-host audit scripts are separate.

Use an isolated checkout and its locked all-extras development environment. The
pure core wheel must match the checkout's Python source. No macOS native image
is transferred. Build the provider on Linux through its self-contained sdist.

```sh
python -m prototypes.native_retention_integration.linux.checks release \
  --core-wheel /path/to/core.whl --output /path/to/release-checks
python -m prototypes.native_retention_integration.linux.checks safety \
  --core-wheel /path/to/core.whl --output /path/to/safety-checks
python -m prototypes.native_retention_integration.run \
  --core-wheel /path/to/core.whl --native-wheel /path/to/linux-provider.whl \
  --python /absolute/path/to/managed/python --output /path/to/gate
python -m prototypes.native_retention_integration.analysis /path/to/gate
python -m prototypes.native_retention_integration.linux.verify /path/to/linux-results --plot
```

Run checks before the serial timing matrix. Reuse the macOS sizes, seeds, overload
and observer controls and source-deleted reproduction cases. `/bin/ps` RSS and
Linux `ru_maxrss` are converted from KiB to bytes. Keep individual peaks separate
from the sampled simultaneous tree peak. No compiler runs in simulation children.

Safety checks fully instrument both engine and binding images in a private
copy-installed environment. Only that environment's manifest/RECORD is updated;
the timed wheel is immutable. Standalone ASan/UBSan enables leak detection;
the CPython boundary disables Python leak reporting and uses `PYTHONMALLOC=malloc`.
No portable-wheel, general Linux/ABI or production release claim follows.

The verification step expects `gate`, `release`, and `safety` directories under
the Linux results directory and the unchanged macOS evidence under its sibling
`results/gate`. It independently regenerates inputs through the common audit,
compares physical results across hosts, reports state/artifact equality separately,
and verifies every base-commit file unchanged. The Linux plot uses the actual
platform label; the original macOS plotting source remains unchanged.
