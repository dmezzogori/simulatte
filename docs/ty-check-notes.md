# Type Check Pass 1 Progress

## Work done
- Ran `uvx ty check` and triaged the 164 original diagnostics.
- Implemented first fixes: narrowed unit-load typing with runtime guard in `StoresController`, adjusted storing policy call, added product/case casts; relaxed some protocols/constructors so tests with fakes pass; broadened coordinate protocol for distance helpers.
- Normalized observable areas by replacing the `exceed` flag with `append_exceed` helpers and updated call sites/tests; added guards for feeding setup when AGV lacks a picking cell.
- Added explicit `n_cases`/product annotations across pallets/layers and tightened requests lead-time tracking to concrete floats.
- Trimmed runner generics and minor exception typing tweaks.

## Next steps
- Revert `AGV.unit_load` widening and re-run narrowing to collapse remaining product/n_cases unions in store flows.
- Resolve list-override LSP errors by removing `list` subclass overrides (swap to composition/helpers) and cascade updates to staging/internal areas.
- Finish feeding log/time property None-guards; tidy WarehouseLocation/PhysicalPosition arithmetic and product typing; address remaining SimPy helper type mismatches (queue, traslo, sequential_store).
- Re-run `uvx ty check` and iterate until diagnostics are cleared, then consider smaller follow-up commits for code changes vs tests.
