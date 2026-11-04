# Installed experimental identity helper gate

The package helper consumes one supplied public managed observation. This fixture
adapts the preceding experiment to it without changing any historical model,
prepared/unprepared implementation, binary parser, validator or evidence.
`native/check_install.py` copies those frozen files byte-for-byte into a temporary
external project and executes `native/identity_probe.py` from a clean wheel install.
They are validation oracles, never package runtime dependencies.

The installed probe compares exact canonical bytes and sizes, fresh callback
reads and callback-only future-state distinctions in full/bounded modes. Both
feedback models run with a query at every poll under helper, prepared, unprepared
and binary-parser consumers. Every artifact and default fingerprint must match.
Four helper Coordinator requests then reproduce after all project Python sources
are deleted. The ordinary install probe independently exercises eight requests,
four source-deleted reproductions and seven ABI/integrity rejection paths.

Run the five-version matrix with a freshly built core wheel:

```sh
uv build --wheel --out-dir prototypes/native_identity_helper/.build/core
uv run --no-project --python 3.14 native/package_matrix.py \
  --core-wheel prototypes/native_identity_helper/.build/core/simulatte-0.17.0-py3-none-any.whl \
  --output prototypes/native_identity_helper/results/macos \
  --artifacts prototypes/native_identity_helper/.build/macos
```

Run the same frozen checkout/core wheel on Linux. This builds host-specific
development wheels and checks integration; it does not select deployment targets,
publish portable wheels or establish a new performance claim.
