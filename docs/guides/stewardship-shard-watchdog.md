# Database shard stack-dump threshold

This correction begins at verified main `c08fd51d`. It changes CI tooling and
its contract tests, not application behavior. The operator-facing description
lives in the [database test guide](stewardship-database-tests.md#parallel-ci-and-live-progress);
this file records the evidence and review.

## Defect

PostgreSQL partition 2 failed two of three consecutive complete CI runs with
exit 245 while every test was passing: run `35473195305` attempt 1 on PR #71
and run `35518503726` attempt 1 on PR #73. Both times the active case was the
unmodified 5,000-Family reference-load test, the log ended at
`Timeout (0:02:00)!`, and the traceback was cut off mid-line. Each cost about
15 minutes and a manual rerun of the failed job.

That case completed in 77.2 seconds on main run `35468347156`, 77.5 seconds in
PR #71's rerun and 120.1 seconds in PR #70's passing run. Exit 245 is the
child's return code -11, SIGSEGV, passed to `SystemExit`. The evidence is
therefore that the interpreter died while the 120-second `faulthandler`
diagnostic was dumping every thread's traceback with the load test's worker
thread live. Both failures were killed at that mark, so the case's slowest
duration is unmeasured.

## Correction

The database shard's threshold is a named five minutes. A contract test keeps
it at least twice the largest hint in any `quality_sharding` map and at most
half the shard deadline. A child killed by a signal is reported by name with a
shell-safe status, and the shard test covers both call sites.

This avoids the trigger; it does not make the dump safe. The load test keeps
its specified reference scale, and browser jobs keep their own 120-second
diagnostic.

## Validation

173 CI-contract cases pass locally in about 17 seconds with no skips. Reverting
either `child_status` call site fails exactly one case. Ruff and Markdown lint
are clean.

## Review rounds

Every round is single-source under the
[September 20, 2026 exemption](../plans/stewardship/overall.md#automated-phase-delivery-cycle):
the Codex reviewer aborted each time with `Your workspace is out of credits`.

### Round 1

Reviewed `e7d39a6f`, the complete diff. Raw Claude severities: two Medium, five
Low below the reporting cutoff. Both Medium accepted and fixed.

- The contract only required a value different from the old one, so 121 or
  1,199 seconds would pass. It now requires headroom on both sides.
- The crash mechanism remained and exit 245 was opaque. `child_status` names
  the signal, and the comment states observed evidence rather than a cause.

### Round 2

Reviewed `3d8ec5c5`. Three Medium, six Low below cutoff. All Medium accepted
and fixed.

- `child_status` was tested only in isolation, so reverting either call site
  stayed green. Signalled baseline and database children were added to the
  shard test; a mutation check confirmed each site is now protected.
- A hand-set slowest-duration constant sat below the measured 121-second hint.
  The bound is now derived from the hints.
- "Over 120 seconds" is a lower bound, not a measurement. The comment says so.

### Round 3

Reviewed `87276853`. No validated finding; no High, Critical or Medium. The
reviewer confirmed the corrections and the bounds, `242 <= 300 <= 600`. Seven
Low notes, five adopted:

- the contract moved to its own named test with a message;
- it now spans all three hint maps, because an exact case hint overrides its
  test's;
- the guide and the sharding comment no longer claim hints never affect an
  assertion;
- the guide documents the named-signal line that replaces exit 245;
- the edited guide paragraph was rewrapped.

Two were recorded as limits rather than changed: hints come from completed
runs and so understate a case that was killed, and a hang beginning in a
shard's last five minutes reaches the deadline before the dump. Both are now
stated in the guide.

The exit criteria are met. Full exact-head CI, DCO and protected delivery
remain required.
