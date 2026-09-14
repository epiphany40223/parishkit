# Ministry response increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-05 plan](../plans/stewardship/parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps) ·
[Family specification](../specs/stewardship/parishioner-portal/spec.md#ministry-stewardship) ·
[Activity policy](../specs/stewardship/admin-portal/spec.md#ministry-activity-management) ·
[Source reconciliation](../specs/stewardship/data/spec.md#parishsoft-refresh-reconciliation)

## Boundary

Branch `pr/stewardship-ministry-responses` starts from PR #26's verified protected
merge, `047f0f458c160fd4f79edc6a57b85f1f32826c1f`. Complete FAM-05.01/.02 and
the Ministry portions of FAM-05.06, with the associated DAT-07 request
derivation, FAM-06/.07 response integration and FAM-03.05 non-census omission.

The coherent outcome is a Family submitting Ministry choices, alone or with
census, including proposed Members when census is enabled. Financial modules,
Staff/leader workflow editing, reports, provider roster writes, deployment and
release are not part of this increment. Gate 2 remains open for financial and
integrated acceptance. Unimplemented financial combinations stay fail-closed.

## Internal checkpoints

1. Add pure Ministry input/answer contracts and enabled-module projection rules.
   Reuse the current local Ministry activity policy and source snapshots; never
   use a different visibility rule in the Family UI.
2. Add durable request derivation and exact-role SQL authority together. Preserve
   hidden requests, immutable provenance and same-intent work across responses;
   resolve requested roster state during the owning source-promotion transaction.
   Audit the fresh-install baseline against the exact starting main without
   modifying retained development databases.
3. Integrate mobile current/leave controls and on-demand searchable join choices,
   proposed-Member selections, complete Review and explicit stale-form choices.
   Ministry-only campaigns omit census editing and census validation.
4. Test source/configuration races, inactive/absent Ministries, hidden history,
   reactivation, terminal Members, proposed UUIDs, namespace isolation, source
   catch-up and current-role boundaries. Run combined response and browser
   regression suites, not just new unit tests.
5. Complete at least three dual-model review/fix rounds, final-head CI and the
   normal protected merge before the following financial increment.

An isolated OPS-09 CI improvement may partition browser validation into parallel
jobs while retaining the required aggregate check and complete test accounting.
Keep fresh browser processes; do not restore the process reuse that previously
hung WebKit. Record such a change as a separate logical commit and validate its
selection/aggregation failure paths.

## Evidence

Planning and interface inspection are in progress. No Ministry implementation
task is claimed complete. PR #26 passed final-head CI with 4,998 default tests,
2,377 PostgreSQL tests, 621 browser cases and 94.02% statement/85.53% branch
coverage; the protected merge and complete merge-group CI are verified. Its
merge-group browser check finished after branch creation, before implementation.
