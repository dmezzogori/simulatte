# Environments and recovery

Opening Studio reads project metadata and saved history. It does not install
dependencies or execute project code. Environment preparation is an explicit action
in **Settings** or the first-run setup card.

## Managed environments

**Prepare environment** uses the project's Python requirements and `uv.lock`.
If a lockfile must be created or updated, Studio shows proposed bytes first. Apply
the preview, then prepare. **Preview dependency update** requests a new resolution;
ordinary preparation uses the accepted lockfile unchanged.

Each generation has a content identity and its own final directory under
`.simulatte/cache/environments/`. Preparation validates the interpreter before
publishing it. It never installs into the environment used by active work.
Cancellation removes only unpublished preparation state without active owners.

**Use environment and restart** activates the prepared runtime after checking Studio
protocol and packaged assets. The launcher relinquishes its writer before the child
opens the store. Simulations, inspections and experiments retain their generation;
selection/cleanup refuses while owners still depend on it. Wait for work to finish
or explicitly stop it before retrying a runtime change.

## Existing interpreter

The advanced setup accepts an existing Python interpreter path. It probes the
runtime without installing packages. The environment must already contain compatible
Simulatte and Studio assets. Metadata drift is checked before new work; this is not
continuous monitoring of every dependency byte. Manage that environment yourself,
or prepare a managed generation for an explicit update.

Interpreter paths are machine-specific store evidence, not authored project model
paths. Do not commit credentials or machine-specific worker configuration into the
project. [Remote worker configuration](../guides/experiments.md) remains user-owned.

## Saved-work recovery

If the selected interpreter disappears, is incompatible, or fails handoff, the
launcher opens a read-only recovery view with an explanation. Supported saved
recordings and generic artifacts remain readable without importing the model or
installing its missing dependencies. Mutation and execution are disabled. A second
writer likewise opens recovery rather than competing for the store lock.

Keep `.simulatte/` intact. Restore an existing interpreter at its recorded path, or
reconstruct a managed generation from its retained evidence using the project API
with a compatible runtime and exclusive store access:

```python
from pathlib import Path
from contextlib import closing
from simulatte.execution.store import RunStore
from simulatte.execution.store_reader import StoreReader
from simulatte.projects.environments import EnvironmentPool

root = Path("studies/plant").resolve()
with closing(StoreReader.open(root / ".simulatte")) as reader:
    evidence = next(g["evidence"] for g in reader.generations()
                    if g["state"] == "selected")
with RunStore.open(root / ".simulatte") as store:
    generation = EnvironmentPool(root, store).reconstruct(evidence)
    print(generation.id)
```

Close Studio before taking writer ownership. Reconstruction leaves project selection
unchanged and can fail if the original Python or packages are unavailable. Preserve
the failure and evidence; do not substitute current dependencies and call the result
the original environment. Reopen Studio after a successful reconstruction.

P1 has an open reconstruction limitation: installing the same locked packages at
a different project path can change generated console scripts and local-wheel
installation metadata. Their distribution `RECORD` hashes then differ, even when
package versions and payload manifest entries match. Reconstruction currently
refuses this mismatch; it does not rewrite captured runtime identities. A local
wheel can also acquire a different uv cache timestamp during reconstruction.
Saved recordings remain readable in recovery. See the
`specs/research/2026-10-19-studio-p1-acceptance.md`
in the repository for the native reproduction and open review issue.
