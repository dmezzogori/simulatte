# Public identity helper at model observation rates

Compare the installed public helper (immutable result plus `to_dict()` at every
observation) with frozen prepared/unprepared consumers. The models, action ledger,
validator, parser and preceding measurement helpers are exact copies of the prior
experiments. No product/provider sources change. The helper remains experimental.

The primary live study uses bounded retention, both feedback models, six seeds,
32,768 control polls and endpoint/every64/every16/everypoll observation rates.
All six consumer permutations occur once per model/rate cell. There are144primary
requests,6fully-disabled controls and2source-deleted reproductions. Query profiling
is separate: full/bounded, both models and65/4096-unit boundaries,8cells. Each has
five shuffled adaptive25ms blocks, separate profiles/traced peaks, and an exact
completed-run comparison with an uninstrumented model.

```sh
.venv/bin/python -m prototypes.native_identity_usage.run \
  --core-wheel prototypes/native_identity_helper/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel prototypes/native_identity_helper/.build/macos/cp314/simulatte_native-0.17.0-cp314-cp314-macosx_27_0_arm64.whl \
  --study live --output prototypes/native_identity_usage/results/live-final
```

Run the same command separately with `--study query --output
prototypes/native_identity_usage/results/query-final`. `--quick` is a preflight;
use a separate parent directory. Run all tests/builds before final timing and keep
sources frozen until both studies finish. Then run:

```sh
.venv/bin/python -m prototypes.native_identity_usage.analysis prototypes/native_identity_usage/results
.venv/bin/python -m prototypes.native_identity_usage.figures prototypes/native_identity_usage/results
```

See `specs/research/2026-11-04-native-identity-usage-plan.md` for declared questions,
controls, timings and limits. The representative rates are workload assumptions;
this is not a production trace or a new cross-platform performance claim.
