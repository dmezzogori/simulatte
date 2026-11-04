# One prepared canonical identity per query

Research consumer optimization against signed `b24538a`. Product/provider code,
default digests, public APIs and earlier files remain byte-identical. Fresh public
hook calls still perform both controller reads and all synchronization/mutation
checks. `projection.prepare()` freezes each fresh view once, reuses immutable
subtrees for the three component hashes, packs the canonical envelope for size,
then detaches plain output. No value is cached across queries. The same independent
validator runs after both candidate and unprepared capture.

Five byte-identical source copies preserve the two fixtures, wrapper, unprepared
consumer and binary parser. `hook` uses the candidate; `unprepared` uses the old
consumer; `reference` uses the parser; `compare` requires exact envelope/size and
canonical byte equality across all three. All modes use the same model source.

Run focused checks:

```sh
.venv/bin/python -m pytest --no-cov -q prototypes/native_prepared_identity/tests
```

With the unchanged matching wheels in
`prototypes/native_provider_projection/.build/{core,native}`, run the query study:

```sh
.venv/bin/python -m prototypes.native_prepared_identity.micro_run \
  --core-wheel prototypes/native_provider_projection/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --native-wheel prototypes/native_provider_projection/.build/native/simulatte_native-0.17.0-cp314-cp314-macosx_27_0_arm64.whl \
  --output prototypes/native_prepared_identity/results/micro-final
.venv/bin/python -m prototypes.native_prepared_identity.micro_analysis \
  prototypes/native_prepared_identity/results/micro-final
```

Run the live gate with the same wheel arguments, using
`-m prototypes.native_prepared_identity.run --output
prototypes/native_prepared_identity/results/live-final`, followed by
`-m prototypes.native_prepared_identity.analysis` and that output path. Finally run
`-m prototypes.native_prepared_identity.figures prototypes/native_prepared_identity/results`.
`--quick` is a preflight, not the final matrix.

The query study reuses54 cells/108paired managed executions and five randomized
adaptive25ms blocks per operation.20 operations now include matched unprepared
composition, identity and complete-pipeline stages; cProfile/tracemalloc passes
remain separate. Candidate and unprepared receive the same fresh hook and validator.
The live study runs72primary cases,12source-deleted reproductions,18controls and
61serial resource/cost cases plus2further reproductions:165unique Coordinator attempts.
Paired dense cases at4096/32768polls use both controllers and three seeds. Scaling
uses4096/32768/262144polls. All existing memory budgets and historical fingerprints/
artifacts must match. Query cost is distinct from end-to-end wall time.

Both runners share one source freeze and verify installed wheel/archive/origin
evidence outside the checkout, with no runtime compiler/prototype imports. They
must run serially; no tests/compilers run during timing. The audits preserve every
base file and all raw measurements. Any performance improvement is experimental
and does not automatically promote this consumer into the product/provider API.
