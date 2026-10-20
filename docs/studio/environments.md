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
project. [Remote worker configuration](../guides/remote-workers.md) remains user-owned.

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

New managed generations use versioned installed-payload evidence. Reconstruction
retains the exact interpreter facts and package/entrypoint content while comparing
recognized generated interpreter paths and installation timestamps independently
of their machine locations. Captured local dependency URLs are bound to verified
retained artifact content before provisioning. Both the original raw probe and the
rebuilt installation probe are kept; reconstruction does not change the selection
or the recorded execution requests. Altered package bytes or launcher bodies refuse
verification even when their RECORD entries were not updated. Studio, and
`simulatte run`, `simulatte run --reproduce`, `simulatte replay`, `simulatte
experiment run` and `simulatte experiment resume` started with the selected managed
generation's own interpreter (for example its `bin/simulatte`), record this evidence
for new requests and verify against the bindings that generation retained from its
own verified build. `experiment resume` never re-prepares: it dispatches the requests
of the stored preparation, and a run whose preparation names a generation resumes only
inside that selected generation.

Older generations and existing interpreters retain their original strict raw-RECORD
identity, as do requests recorded by `simulatte run` or `simulatte experiment run`
from any other interpreter. They are not silently upgraded. Reinstalling their
packages may still fail verification because console-script paths, local URLs or
cache metadata differ.
Insufficient old evidence remains unavailable for verified reconstruction; saved
recordings remain readable in recovery. Unsupported launcher/evidence formats also
refuse rather than falling back to a weaker identity. Native platform verification
and review status are recorded in
`specs/research/2026-10-19-studio-p1-acceptance.md` in the repository.

Moving a project to another machine, and the difference between offline viewing and
execution verification, are described in
[Storage and portability](storage.md#move-or-copy-a-project).
