# ODD Static Routing Denominator Design

Date: 2026-05-20

## Context

`simulatte.dispatching_rules.basic.odd` currently computes the operational
due date denominator from the number of operations whose `servers_exit_at`
value is still `None`. That makes `|R_i|` shrink during a job's lifecycle.
For later routing steps this can push the computed operational due date above
the job due date after upstream operations complete.

The intended interpretation is:

```text
o_ij = t_r + n_ij * max(0, (d_i - t_r) / |R_i|)
```

where:

- `t_r` is the shop-floor entry time, using `job.psp_exit_at` when set and
  `job.created_at` otherwise.
- `n_ij` is the static 1-indexed routing-step number of the requested server.
- `d_i` is the job due date.
- `|R_i|` is the fixed routing length when the job enters the shop floor.

## Approved Approach

Use `len(job.servers)` as the fixed denominator in `odd`.

This matches the current codebase because production job routes are stored as
an ordered server sequence and are treated as static after job creation. It
keeps the behavior change local to the dispatching rule and avoids adding a
new job state field that would duplicate existing route information.

## Behavior

For a job with five servers, `|R_i|` remains `5` for every operation due date
calculation, even after one or more upstream operations have exited.

Negative slack remains clamped by the existing `max(0, ...)` term. Jobs with no
servers are not expected to reach queue dispatching, but the implementation
will keep a defensive zero-denominator guard.

## MODD Impact

`modd` is defined as:

```text
m_ij = max(o_ij, now + p_ij)
```

It already calls `odd(job, server)`, so no direct `modd` implementation change
is needed. Its ODD component will inherit the fixed routing-length behavior.

## Tests

Update the basic dispatching-rule tests to:

- Replace the old dynamic remaining-count ODD test with a regression test that
  marks an upstream server as exited and verifies the downstream ODD remains
  based on the original route length.
- Add or adjust MODD coverage so completed upstream operations do not change
  the ODD branch used by MODD.
- Keep existing coverage for PSP release time and negative-slack clamping.
