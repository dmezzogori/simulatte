# Dispatching Rules: Reorganize by Scheduling Family

Date: 2026-05-28

## Motivation

The `simulatte.dispatching_rules` package is currently split into `basic.py`
(stateless functions) and `parametrized.py` (allowance factories) — an axis based
on *implementation shape* rather than meaning. To find `critical_ratio` a reader
must first know it is "basic" and not "parametrized." Two observations drive this
change:

- The rules are general `(job, server) -> float` scoring functions, usable as a
  sort key anywhere — not only for queue dispatching. `Slar`, for instance,
  already uses `planned_slack_time` both as the router's queue priority and to
  rank PSP candidates when deciding which job to release. The implementation-shape
  split obscures that these are one coherent family of priority functions.
- Whether a rule happens to be a plain function or an allowance factory is an
  internal detail that should not dictate where it lives.

## Approved Design

Group rules by scheduling family. The public API is unchanged.

```
dispatching_rules/
├── __init__.py     # re-exports all 8 names; __all__ unchanged
├── processing.py   # shortest_processing_time, first_come_first_served
├── due_date.py     # earliest_due_date, operational_due_date, modified_operational_due_date
└── slack.py        # critical_ratio, planned_slack_time, slack_per_remaining_operation
```

- `basic.py` and `parametrized.py` are removed.
- `__init__.__all__` and every public import path
  (`from simulatte.dispatching_rules import <name>`) are preserved exactly.
- `modified_operational_due_date` and `operational_due_date` now share a module,
  so the former's call to the latter becomes a local reference (one fewer
  intra-package import).
- `slack.py` intentionally holds both a plain function (`critical_ratio`) and two
  factories (`planned_slack_time`, `slack_per_remaining_operation`): family, not
  implementation shape, is the organizing axis.
- Family-placement judgment calls (deliberate, slightly loose): `first_come_first_served`
  lives in `processing.py` as the trivial local/baseline rule (it is really
  arrival-order), and `critical_ratio` lives in `slack.py` as the ratio member of
  the slack family.
- Each module gets a short module docstring naming its family; the `__init__`
  docstring drops the Tier-1/Tier-2 `.basic`/`.parametrized` framing and points at
  the family modules instead.

## Tests

Mirror the source layout:

```
tests/core/
├── test_processing_rules.py   # SPT, FCFS
├── test_due_date_rules.py     # EDD, ODD, MODD
├── test_slack_rules.py        # CR, PST, SOPN
└── test_dispatching_rules_integration.py   # unchanged
```

- `test_basic_rules.py` and `test_parametrized_rules.py` are removed; their test
  cases move into the family files unchanged (they already import at the package
  level, so no test body edits are needed beyond relocation).
- The integration suite (`test_dispatching_rules_integration.py`) is untouched.

## Non-goals

- No behavior change. This is a pure move/regroup; rule formulas are untouched.
- No signature change. The `(job, server)` contract stays; a pure `(job) -> float`
  variant is explicitly out of scope.

## Verification

The existing test suite is the regression safety net for the move. All of the
following must pass:

- `uv run ruff check src tests` and `uv run ruff format --check src tests`
- `uv run ty check src`
- `uv run pytest` (>= 99% coverage gate)
- `uv run zensical build` (docstring `:mod:` references repointed to the new
  modules; cross-references still resolve)
