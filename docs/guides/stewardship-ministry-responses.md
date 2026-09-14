# Ministry response increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-05 plan](../plans/stewardship/parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps) ·
[Family specification](../specs/stewardship/parishioner-portal/spec.md#ministry-stewardship) ·
[Activity policy](../specs/stewardship/admin-portal/spec.md#ministry-activity-management) ·
[Source reconciliation](../specs/stewardship/data/spec.md#parishsoft-refresh-reconciliation)

[Review rounds and corrections](stewardship-ministry-responses-reviews.md)

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

Implementation, local validation and all three dual-model review/fix rounds
are complete. Final-head CI and the protected merge remain pending. Gate 2
still requires the financial increment and integrated acceptance. PR #26
passed final-head CI with 4,998 default tests,
2,377 PostgreSQL tests, 621 browser cases and 94.02% statement/85.53% branch
coverage; the protected merge and complete merge-group CI are verified. Its
merge-group browser check finished after branch creation, before implementation.

The corrected implementation passes 5,037 default tests, 336 combined response/
census/authority/schema PostgreSQL cases in 321 seconds, and 192 Family browser
cases in 233 seconds. The final Ministry file has 18 cases across Chromium,
Firefox and WebKit. After round 2 added three direct-SQL fault cases, all 66
selected Ministry/Member-authority/schema cases passed in 100 seconds. That
round changed tests only; earlier application/browser results still apply.
Ruff, full tracked Markdown and model-state drift checks pass.

Coverage includes both enabled module combinations, exact web/worker roles,
same-intent succession, withdrawal, hidden requests, proposed UUIDs, terminal
Members, activity changes, positive join/leave roster catch-up, independent SQL
rejection, module-toggle census continuity and stale withdrawal acknowledgement.
The [review ledger](stewardship-ministry-responses-reviews.md) records all raw
severities, accepted corrections and duplicate disposition; round 3 approved
with no validated Medium-or-higher findings.

Earlier validation exposed a configuration-checkpoint fixture error after 67
passes; its isolated 38-case rerun and subsequent 291-case combined rerun both
passed without weakening or omitting it. An intermediate 263-pass attempt
also exposed four new CHECK-constraint cast renderings differing from the
baseline's normal PostgreSQL dump/reparse representation. Correcting that
representation preserved all predicates and the independent model checker;
all model-contract/negative-probe and strict baseline checks now pass.

### Fresh-install schema audit

A separately named reference database was installed from exact starting main
`047f0f458c160fd4f79edc6a57b85f1f32826c1f` and verified against that commit's
independent strict fingerprint before comparison. No retained development
database was deleted, upgraded, downgraded or repurposed.

The reviewed delta adds one `stewardship_ministry_request` relation, 16 columns,
27 constraints, seven indexes, nine functions and three triggers. No existing
columns, constraints, indexes, policies, relations or triggers changed or were
removed. Five existing functions change deliberately: final aggregate
validation, census predecessor authority, worker source-pin admission, and the two retained-response pin
reference checks. All 28 row policies remain unchanged. The fresh schema has
129 relations and 312 functions; Django reports no model-state drift.

The workflows app has one initial state-only migration; its table, constraints
and guards are part of the shared fresh-install SQL baseline. Ministry requests
derive in the existing ordered final-submission transaction. Deferred checks
reject missing derived choices and abandoned predecessor work. Worker resolution
requires a fenced current snapshot, exact Family ownership, current catalog
presence and the requested roster state, plus a retained provenance pin.

Hidden Ministries and missing/foreign Members preserve prior requests. A
confirmed terminal answer withdraws visible unresolved Ministry choices, as
does removing an editable proposed Member; neither action writes a source
roster. Source catch-up may resolve hidden work with actual roster proof.
Staff assignment/contact editing remains with the later workflow increment.
