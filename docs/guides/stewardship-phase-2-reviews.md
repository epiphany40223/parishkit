# Phase 2 review ledger

This ledger follows the [delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [Phase 2 scope](stewardship-phase-2.md). Raw review outputs remain local,
not repository content. Neither a correction checkpoint nor an interim review
completes Phase 2 acceptance or authorizes merging.

Current handoff: all three complete-phase review/fix rounds and final local
acceptance pass at corrected implementation `ea2d5cb`. See the
[acceptance index](stewardship-phase-2-acceptance.md) for final test/coverage
evidence. Earlier sections below are chronological checkpoints, not unresolved
work. PR CI and human merge approval remain required.

## Round one: interim source/setup correction pass

Reviewed commit: `1619866414d04a33d57f71e4e55524b49371ad49`.
Base: PR #21 merge `48be3666f0c89cc15586cb67465cd1ba0504203c`.
Session: `20260912-125902-d5675a`.
All 26 Pika-generated Claude shards and the single detached Codex reviewer
completed successfully. Finalization has no failed agents, degradation, verdict
mismatch or salvage requirement. The result is **request changes**: 86 retained
findings (six High), comprising one agreed, 84 Claude-only and one Codex-only.

Identifiers below use that finalized bucket order, with each bucket retaining
its emitted order. Review and correction are still in progress. Implemented
corrections awaiting PostgreSQL evidence are not
recorded as validated or as completing a review/fix round.

| ID | Severity | Finding | Current disposition |
| --- | --- | --- | --- |
| 1 | Medium | Undrained renewal is caught as an ordinary broker error | Implemented: fatal drain boundary, explicit 30-second process-drain budget and closed diagnostic. Broker-boundary tests preserve a pre-existing handler exception as context while preventing consumer continuation. |
| 2 | High | One broken task discards the scheduler page and starves later work | Implemented: isolate stale/invariant/missing-object scope failures per row, retain a closed diagnostic and advance the cursor. Database-outage failures remain service-level. PostgreSQL evidence pending. |
| 3 | High | SQL fact liveness argument collides with the fence column | Validated: a forward migration binds positional task/fence/worker arguments. Direct SQL tests reject each mismatched component; recovery tests retain exact ownership. |
| 4 | High | Explicit blank should fall back to an older contact value | Auto-skipped, false-positive: `test_explicit_empty_search_value_does_not_fall_back_to_older_contact_email` explicitly protects a source-cleared email. Missing and verified-blank values are intentionally different; fallback on falsiness would resurrect cleared data. |
| 5 | High | Lost credential-installer interlock rejects the candidate | Implemented: owner-check failures cross a fatal, private-text-free boundary; an already spawned helper is drained. The candidate is neither rejected nor installed. PostgreSQL sealed-intake evidence pending. |
| 6 | High | Giving lookup merges DUID and parish-local Family ID spaces | Implemented: use the existing shared `link_family_pledges`/`link_family_contributions` DUID contract only. Tests cover conflicting local IDs and refuse local-ID fallback for unknown DUIDs. |
| 7 | High | Household fallback omits death date | Auto-skipped, false-positive: the published Family Member-list DTO has no death-date field to copy; the Member detail endpoint returns `MemberSearchResponseDto`, which already includes `dateOfDeath`. Inventing a fallback field does not fix a demonstrated provider path. |
| 8 | Medium | Dashboard failure links open JSON rather than the task page | Implemented: use `background_task_page`; navigation regression pending. |
| 11, 14 | Medium | Same dashboard link | Auto-skipped, duplicate of 8. |
| 12 | Medium | Retained-content view has no request tests | Auto-skipped, false-positive: `test_content_history_postgresql.py` covers actual HTTP, real web grants, archived branding/content, revision scope, empty content, non-Admin refusal and restore hold. |
| 13 | Medium | Credential-selection authorization has only pure helper tests | Auto-skipped, false-positive: `test_integration_selection_postgresql.py` exercises web/installer SQL identities, exact ACK proof, settings/stale-base/freshness refusal, pending replacement, actor isolation and migration replay. |
| 15 | Medium | Source rejection has no takeover/refusal tests | Auto-skipped, false-positive: `test_source_rejection_postgresql.py` covers newer-owner rejection, live external-read hold, idempotent admission, forensic immutability, audit rollback, promotion race and migration replay. |
| 17, 38 | Medium | Configuration unavailability is misclassified as invalid input | Implemented: the shared mapper handles `ConfigError` before its `ValueError` superclass, returning safe 503 plus Retry-After; field errors remain 400. Pure regression passes; HTTP expectations pending. |
| 19 | Medium | Manual source-progress button becomes inert after polling stops | Validated browser correction: preserve the native POST when automatic polling is no longer live. |
| 21 | Medium | Family presence SQL uses the 30-minute Admin idle allowance | Validated: the forward migration uses 60 minutes. Real web-role heartbeat and SQL tests accept 45-minute idle sessions and refuse 61-minute idle sessions without renewing activity. |
| 23 | Medium | Share labels accept controls rejected by the YAML schema | Validated: form uses the same canonical text check; tab, C1 and DEL tests reject the label before candidate construction. |
| 25 | Medium | Same Family presence idle mismatch | Auto-skipped, duplicate of 21. |
| 29 | Medium | Progress HTTP boundary tests run as schema owner | Implemented: hidden-field and watchdog request assertions now use actual web-role grants. PostgreSQL evidence pending. |
| 30 | Medium | Branding preview does not recheck its independently reread YAML base | Implemented: absent or changed base raises staleness before candidate construction. Regression pending. |
| 33 | Medium | Drain exception masks an existing handler exception | Resolved with 1: fatal non-drain must take precedence over an ordinary handler exception; a closed diagnostic is emitted and the original exception remains chained. Both handler-success/failure broker paths pass. |
| 39 | Medium | Optional branding can break error pages when runtime/storage is absent | Validated: catch the explicit limiter and filesystem unavailability classes as well as configuration/database failures. No blanket exception suppression. |
| 42 | Medium | Same optional-branding error boundary | Auto-skipped, duplicate of 39. |
| 47 | Medium | Cancellation can rewrite a completed setup attempt | Auto-skipped, false-positive: the SQL guard prohibits terminal rewrites and currently refuses completion entirely until its finalization owner exists. The proposed intermediate completed-but-bootstrap state is not an implemented transition. Finalization must retain atomic marker ownership. |
| 55 | Medium | Progress docstring implies GET commits expiry | Corrected documentation: GET remains passive; POST commits expiry and the scheduler independently fences abandoned staging. Existing PostgreSQL tests explicitly distinguish both paths. |
| 70 | Medium | `hasMembers` should be boolean | Auto-skipped, false-positive: the published `FamilySearchResponseDto` declares `hasMembers` as int32, unlike the neighboring boolean flags. |
| 79 | Medium | Renewal could return success after a login-binding change | Implemented defensive final binding check; the renewal transaction rolls back if authentication changes the attempt's original session ID. PostgreSQL regression pending. |
| 85 | Medium | Member detail/search DTO equivalence is unsupported | Auto-skipped, false-positive: the published GET Member response and POST Member-search items both reference the exact same `MemberSearchResponseDto`. |
| 86 | Medium | Browser clock skew prematurely stops setup renewal | Validated: render/return server time and advance it using monotonic browser elapsed time. Tests cover clocks 48 hours ahead/behind and a later seven-day wall-clock jump. |

Additional dispositions from the same round (not an additional review round):

| ID | Severity | Finding | Current disposition |
| --- | --- | --- | --- |
| 9, 10 | Medium | Repeated payload parsing and caller-dependent SQL name resolution | Validated: forward migration parses each canonical insert once and pins/qualifies all three source guards. A real PostgreSQL test shadows source relations with temporary tables and still stages against the genuine manifest/lease/payload. Full source reversal/reapply passes. |
| 16 | Medium | Setup SQL events missing from Python vocabulary | Validated: add the five implemented setup events and compare the installed audit function with the enum. The claim that every other SQL event already belonged to this enum was inaccurate, but explicit setup vocabulary is useful. |
| 18 | Medium | Duplicate Family presence section vocabulary | Validated: the model constraint and endpoint derive from the same ordered vocabulary; existing section/SQL tests pass. |
| 20, 26 | Medium | Repeated dashboard configuration hydration | Validated: the dashboard lends its verified projection to presentation-only context processors for that response. No authorization check consumes this hint; no global cache is added. The existing reference-population query budget passes unchanged. |
| 24 | Medium | Automatic header polling writes report-view audit events | Validated: a separate counts-only endpoint shares authorization and final revocation checks, but fetches no task identities and records no report read. Explicit list/detail reads retain audit records. Real web-role and passive-session tests pass. |
| 28 | Medium | Misleading column restriction on public projections | Validated: remove the overridden Parish column map and document the complete read needed for canonical YAML/SQL startup parity. Grants are not broadened; existing private-table and runtime-role tests pass. |
| 32 | Medium | Setup/session deadline copies can drift | Validated: share pure session policy constants and test installed setup guard intervals. Changing policy still requires an explicit SQL migration. |
| 34 | Medium | Missing ParishSoft integration leaks an ORM exception | Implemented: bounded optional fingerprint lookup raises the same scope denial as a changed credential; explicit missing-integration regression pending. |
| 43 | Medium | Stale promotion test fails for missing lock, not changed campaign | Validated: test holds the real work-order lock while deliberately bypassing the service admission already tested separately. Using `execution.effect()` as suggested would fail at that earlier service check and still not exercise SQL. |
| 46 | Medium | Content history retains every full body | Validated: retain a fixed 32-byte canonical-value digest per revision identity instead of references to all bodies. Repeated and mutated revision checks plus database ancestry tests pass. |
| 48 | Medium | Stale credential receipt reported as unavailability | Validated: stale receipt/settings evidence maps to 409. An in-flight replacement remains a retryable 503, not a stale or invalid-input result. |
| 51 | Medium | Inactive selected funds disappear from draft editor | Validated: both pledge and comparison selections retain their inactive, labeled fund; an unrelated draft name edit succeeds under actual web grants. |
| 53, 57 | Medium | Frozen setup supposedly permits transitions and misleading audit events | Auto-skipped, false-positive: `OLD.state='frozen' OR ...` is the condition for raising an exception, not for allowing the update. New PostgreSQL tests reject all three claimed transitions with owner, arbitrary and null attribution and assert no audit event was added. |
| 56 | Medium | Ready generation bypasses demand claim liveness | Validated: check the demand's exact live claim before transfer, even after generation publication. Same-owner replay and abandoned-owner recovery pass; a live rival receives `FactUnavailable`. |
| 59 | Medium | Terminal branding cleanup has no explicit retry path | Implemented: admit explicit retry under existing eligible/unpinned/configuration gates while retaining one automatic root per bundle. Completed-command replay performs no new file mutation. PostgreSQL evidence pending. |
| 65 | Medium | Delta compares raw group names with normalized retained names | Validated: use the full-load scalar canonicalization; whitespace, nonbreaking-space and decomposed-accent regression cases preserve delta equivalence. |
| 67 | Medium | A new configuration hold prevents cleanup failure settlement | Implemented: exact-claim failure acknowledgement does not authorize file IO and can settle after admission closes. PostgreSQL evidence pending. |
| 68 | Medium | Wrong-typed chair identity map looks like missing bindings | Validated: require UUID keys and typed seed identities. A genuinely empty binding map still follows the established fail-closed policy. |
| 69 | Medium | Content reorder can trigger changed-content admission | Implemented: compare ID-keyed records before running mutation gates. Explicit no-current/held reorder regression pending. |
| 73 | Medium | Preview date placeholders use machine-format civil dates | Validated: sample substitution uses the shared civil-date formatter without timezone shifting. Stored templates remain unchanged. |
| 74 | Medium | Retained credential requests select a future emitted parser | Validated: freeze the v6 request's supported public-schema selection. Replay produces the identical candidate even when the current emission selector changes. |
| 77 | Medium | Partial stdin write can stall provider validation | Validated: one bounded private pipe exchange runs while the supervisor checks ownership, then must be killed/reaped/joined before descriptors close. Reserve drainage in the request TTL. A real offline subprocess delays reading a pipe-capacity-exceeding payload and completes; 55 provider tests pass. |
| 80 | Medium | Credential filesystem permission error becomes an ordinary source hold | Auto-skipped, false-positive: `SourceCredential.read()` delegates to `read_private()`, which converts every filesystem `OSError`, including `PermissionError`, to a closed `CryptographicError` before this classifier. |
| 82 | Medium | Exhausted shared retries skip source failure classification | Validated: unwrap bounded/cycle-checked retry wrappers before all classification branches and recognize shared transient transport types. Unknown ownership/drain failures remain unclassified. |

Provider contract evidence was checked on September 12, 2026 against the
[published v2 OpenAPI document](https://ps-fs-external-api-prod.azurewebsites.net/swagger/v2/swagger.json).
The raw provider schema is not committed. Giving identifier semantics follow
the existing shared ParishKit linking functions rather than an inferred local-ID
alias based only on the provider's inconsistent field capitalization.

Further dispositions and corrections from the same round:

| ID | Severity | Finding | Current disposition |
| --- | --- | --- | --- |
| 22 | Medium | New ancestry queries hardcode model identifiers | Validated: content/Ministry queries derive quoted tables and columns from model metadata while binding all submitted values. Existing PostgreSQL retained-content and identity tests pass. |
| 27 | Medium | Recovery repeatedly reloads the same request and lease | Validated: a call-local lazy evidence object shares immutable observations only during one locked decision. New-work admission retains its independent authorization read. SQL query bounds and stale-after-transition refusal pass. |
| 31 | Medium | Presence uses the work-order lock | Retained with documented invariant: mode/epoch, Family eligibility and source-name lookup must form one observation. Plain READ COMMITTED does not provide it. Pages remain bounded; concurrent mixed-load evidence is still required by ARC-08, not inferred from a population-size test. |
| 35 | Medium | Compaction orphan discovery can scan retained payload history | Operational integration remains open under OPS-07.02/.05. Deleting only payloads from this call's removed memberships, or skipping empty membership batches, would strand older orphans when the payload deletion limit is reached. No compaction runtime is enabled in Phase 2; the later owner must bound discovery as well as deletion and prove residual cleanup. |
| 36 | Medium | Per-row source guards may be costly at scale | Corrected repeated dynamic planning with nine static typed lookups; each inserted row still checks the wall-clock Task/source lease. A real PostgreSQL benchmark stages 18,000 payloads/memberships in 500-row batches within a 30-second total and 10-second batch bound. The 33-case storage/credential batch passes. This is storage evidence, not the remaining complete provider/Compose import demonstration. |
| 37 | Medium | Tenant revalidation failure does not trigger full fallback | Auto-skipped, already handled by fail-closed tenant admission: a key no longer proving the expected organization cannot authorize a larger full read. Feed ambiguity is distinct from credential/tenant failure. Clarified the shared API contract and added a real coherent-client regression proving no subsequent collection fetch. |
| 40 | Medium | Historical content and work-hold admission lack tests | Validated: real archive/Return-to-Testing fixtures test absent/different current ownership, then isolate a real preparing work gate. Guards remain enabled. |
| 41 | Medium | Held abandoned source roots repeatedly publish hints | Validated: abandoned recovery hints require an actionable current recovery plan. Expired running claims still receive hints so SQL can fence them. A reserved external-read deadline suppresses publication until drainage is proved. No task history/deadline is rewritten merely to throttle hints. |
| 44 | Medium | Recent retention anchors become a large in-memory ID exclusion | Validated: filter the recent interval in SQL before calculating older anchors and candidates. Retention/race tests pass, including exact inspected metadata population. |
| 45 | Medium | Temporarily unavailable branding/credential dependencies permanently fail configuration | Validated: typed readiness unavailability leaves the same request retryable with no failed checkpoint. Actual writing-to-ready branding and later successful installation pass. |
| 49 | Medium | Outcome predicates could use a dispatch mapping | Auto-skipped, negligible impact: no incorrect action/state outcome was identified. A second mapping/function layer is not required for correctness; bounded proof sharing and explicit recovery-hint handling address the concrete defects without changing the default-deny policy. |
| 50 | Medium | Dashboard holds work serialization through template rendering | Validated: retain coherent bounded database observation and audit under the work lock, then release it before rendering. Final authorization runs after rendering. Tests inspect the backend lock inventory and refuse a concurrent revocation; the existing 5,000-Family/100-session query and latency baseline passes. This is not a simultaneous mixed-workload claim. |
| 52 | Medium | Credential-selection preview renders under the work lock | Validated: capture exact base/proof under the lock, then construct and render the immutable preview outside it. Real web-role tests inspect the backend lock inventory at both operations. Confirmation still revalidates current evidence. |
| 54 | Medium | Request and attempt SQL duplicate refresh-window derivation | Validated: forward migration makes request admission call the existing window helper, also pins its search path. Installed-function contract and financial/current-scope PostgreSQL tests pass. |
| 58 | Medium | Unknown provider-helper errors reject otherwise valid credentials | Validated: ambiguous adapter/DTO/transport failures report unavailable with no private exception text. Invalid is reserved for malformed candidate input or explicit authentication refusal. Slack and SMTP transient/error responses are distinguished; all 56 provider tests pass. |
| 60 | Medium | Missing or malformed chair tenant settings raise incidental exceptions | Validated: derive the canonical bounded organization before querying, with a closed storage-invariant error for unavailable scope. Missing, wrong-shaped and noncanonical cases pass. |
| 61 | Medium | Credential expiry is based on form-render time and too short for consumer recreation | Validated: the server chooses a one-hour lifetime at first intake under the reservation lock. Exact retries reuse the durable expiry, including after expiry; the five-minute fresh-auth/form-intent limits remain unchanged. The form explains the operator deadline. |
| 62 | Medium | Provider context RLS exempts the table owner | Validated: new migration forces RLS while preserving existing target read/intake policies. Installed flags, actual restricted-role context tests and migration reversal/reapply pass. |
| 63 | Medium | Reserved future Valkey identities differ from currently provisioned services | Auto-skipped, already handled by phase ownership: only implemented web/worker/scheduler services are provisioned and only supplied passwords enter the server ACL. Reserved mail/backup identities are not launched or granted access; their owning phases must add provisioning with real consumers. |
| 64 | Medium | Changed handoff keys block installer startup without a distinct diagnostic | Validated: emit the closed `credential_handoff_key_mismatch` event before refusal, without key values. The installer runbook requires restoring the original matching key; automatic public-key replacement would strand retained ciphertext and is not an authorized rotation protocol. |
| 66 | Medium | Out-of-query contributions reject the whole observation | Retained: the cited uncertainty concerns pledge creation/effective-date semantics, for which the code already avoids date filtering. Contribution query-contract violation cannot prove complete in-window coverage. Silently dropping unexpected records could accept an incomplete observation. Existing tests intentionally refuse ignored contribution filters. |
| 71 | Medium | Roster identities collide for different null-ID roles | Validated in part: a null role ID now falls back to the role name, with regression coverage. End date remains mutable stint content rather than identity; changing it preserves the same row key. Genuine duplicate logical identities still refuse whole promotion instead of silently publishing partial source truth. |
| 72 | Medium | Pin/compaction runtime grants are absent | Auto-skipped for Phase 2, already handled by phase ownership: fact builders/pins and source/fact retention are storage primitives, not registered runtime executors. Actual grants belong with the Phase 4/5 fact/export consumers and Phase 6 OPS-07 compactors. Do not pre-grant PII deletion to the ordinary source worker. |
| 75 | Medium | Unexpected dashboard exceptions expose framework error pages | Auto-skipped, false-positive: the outer security middleware normalizes unmarked framework 500 responses, including DEBUG pages. Prepared configuration validation covers missing projections and zero-denominator percentages are supported. New injected-defect HTTP tests verify the common boundary rather than misclassifying programming errors as ordinary availability failures. |
| 76 | Medium | Scheduler setup guard inspects NEW before rejecting DELETE | Validated defensive forward migration: deletion is rejected first. The real SQL delete regression checks the explicit history message and SQLSTATE 23514. |
| 78 | Medium | Scalar Valkey password paths change meaning during topology generation | Auto-skipped, false-positive: both provisioning and rendering first call `resolve_valkey_files`, which binds the scalar to the original service role and rejects unsupported/ambiguous use before per-service derivation. The finding omitted this normalization. |
| 81 | Medium | Disabling financial resets custom sharing options silently | Validated: the exact change preview explicitly warns that re-enabling starts with defaults; retained configuration history remains untouched. A real HTTP regression confirms warning-before-application and unchanged existing labels. |
| 83 | Medium | Failed/final fact generations have no deletion path | Auto-skipped for Phase 2, already handled by later ownership: OPS-07/DAT-09 must add their exact fenced retention and exceptional-purge disposal rules. Current refusal is intentional until those owners exist, not permission to bypass protected history. |
| 84 | Medium | An abandoned refresh root can absorb manual requests | Retained with clarified semantics: actual running claims, retained source leases and promoted roots are excluded. An abandoned root with none of those remains waiting on its recovery dependency/admission; receipts expose its actual status, not claimed running work. Creating more roots would not resolve that hold. Finding 41 removes repeated broker publication while held. |

The new credential sequencing regression also found and fixed an additional
problem: after a credential was installed/acknowledged but before its fingerprint
was selected in YAML, a second replacement could bind the obsolete YAML
fingerprint as its rollback predecessor. Sealed intake now rejects that second
replacement under the target-reservation lock. The end-to-end test then selects
the first credential, stages/cancels another, and proves cleanup retains the
actual working bytes. Reusing an intake identity with different contents is a
typed invalid request, not transient service unavailability.

Subsequent passing PostgreSQL batches validate the earlier pending entries
2, 5, 8, 17, 29, 30, 34, 38, 59, 67 and 79. Content reorder entry 69 is now
covered with genuine archived history and a work hold. These later results
supersede those rows' earlier pending-evidence checkpoints.

Focused correction evidence includes 76 worker/provider/broker tests, 102 source
giving/loading/corpus/household tests, 38 admin-error/share-form tests and 12
source-progress browser cases across three engines. Later focused suites pass
55 provider tests, 36 delta/failure tests, 53 content/schema-selection tests,
40 chair/failure tests and 32 selection/content-form tests.

The full diagnostic PostgreSQL run reported 1,743 passes and seven failures.
The first correction batch reported 221 passes and four fixture/status failures;
the next reported 79 passes and one incorrectly isolated SQL test. Those
failures were corrected. Subsequent focused PostgreSQL batches pass all 117
source/task/grant/content tests and all 47 setup/progress/campaign-editor tests.
The earlier source/fact/scheduler/branding/progress corrections also passed their
focused cases within those diagnostic batches. Counts overlap and must not be
summed into a full-suite claim. Full post-correction regression and the remaining
triage are still required; this does not complete a review/fix round.

The current full browser run passes all 318 cases across three engines. The
later baseline passes 3,765 tests with 2,150 explicit profile skips and two
existing warnings; it does not replace PostgreSQL/browser/container evidence.
Further focused PostgreSQL batches pass 49 cleanup/selection/presence cases,
48 source-attempt/branding/selection cases, 18 retention/race cases, 33
source-outcome/handoff/content cases, 76 request/attempt/campaign-editor cases,
and 15 dashboard/identity-performance cases. The subsequent full PostgreSQL
run finished with 1,793 passes and five failures: three outdated dashboard-error
assertions and two missing SQL admissions for the new handoff-key diagnostic.
Those are corrected, and the focused rerun passes all 78 cases. These overlapping
focused results do not replace a clean full-suite run or complete a review/fix
round.

## Setup finalization specification decision

The [wizard contract](../specs/stewardship/admin-portal/spec.md#bootstrap-and-first-admin-wizard)
requires cancellation cleanup before the configured marker, including after
authoritative YAML selection. The
[architecture contract](../specs/stewardship/architecture/spec.md#configuration-and-secrets)
previously permitted selected-but-unapplied cancellation only for exceptional
campaign end edits. On September 12, 2026 the owner explicitly approved extending
that journaled exception to initial setup. The specifications and ADM-02 plan now
require exact original-attempt/predecessor binding and atomic database activation
with the configured marker. This decision resolves the specification conflict;
implementation remains open. It does not authorize applied-configuration rollback,
broader credential grants or a setup-completion claim.

### Approved setup cancellation implementation checkpoint

The explicit bootstrap-to-configured intent parser preserves every bootstrap
Administrator and rejects ordinary/default intake, partial profiles, replaced
identities and malformed private values. Its 60-case combined parser regression
passes. SQL now requires an atomic original-attempt/request binding and retains
immutable setup-abort decisions. Selected-candidate recovery checks original
expiry, journals before file restoration, and runs before forward recovery.
The configured-marker/source/Family activation owner remains deliberately closed.

An original-login-only `/admin/setup/cancel` page can cancel finalization across
the selected-but-unapplied mismatch without admitting other configuration edits.
Real web-grant HTTP tests cover passive reads, CSRF, another login, private-value
exclusion and durable cancellation. The restricted configuration installer can
consume that expiry proof without session or source-payload access. Temporary
logos stay pinned through interrupted restoration; actual scheduler/worker tests
prove cleanup after the failed checkpoint and retention when another prepared
configuration still references those assets.

The integrated cancellation/configuration batches pass 74 and 25 cases; the
latest cancellation/HTTP/branding-worker batch passes all 33 cases. Ruff and
formatting pass, and the latest baseline passes 3,792 tests with 2,172 explicit
profile skips and two existing warnings. Counts overlap. Full current PostgreSQL,
browser/container acceptance, remaining wizard work and the phase review rounds
are still required.

### Subsequent regression and credential-intake checkpoint

The complete PostgreSQL regression at the cancellation checkpoint passed all
1,815 tests in 18 minutes, 25 seconds. Later focused runs cover the additional
static source lookup, worker acknowledgement and sealed wizard intake changes;
the full result does not claim to cover code added after that run began.
The source review corrections are committed as cd4ea30.

The worker/scheduler acknowledgement command now checks the already-running
process cohort and actual mounted bytes without manufacturing replacement
receipts. Its restricted-role tests reject stale process evidence and preserve
ciphertext exclusion. The temporary setup-source cryptographic handoff has
original-attempt, credential, Task, worker and source-fence binding; its private
recipient lives only in memory. This primitive is not yet a registered exchange
or setup loader.

Wizard-only credentials now have their own original-login storage and
write-only HTTP forms. They cannot enter the live replacement queue. Collecting
edits use the entire attempt's optimistic version; loading refuses replacement.
Terminal expiry/cancellation atomically clears ciphertext and public provider
settings, retaining only safe receipts and redacted audit events. Web and
scheduler ciphertext reads are denied by column grants; SQL repeats ownership,
fresh-authentication, closed settings and terminal-scrub checks.

The integrated intake/HTTP/cancellation run passed 65 tests with 93% focused
coverage. The later audit/scrub-settings batch passed 35 tests, followed by
68 setup, discovery, credential-isolation and migration cases. Six new
Chromium/Firefox/WebKit mobile/desktop component checks pass. Counts overlap.
After correcting two outdated acknowledgement-grant assertions, the full
baseline passes 3,825 tests with 2,210 explicit profile skips and two existing
warnings. Ruff, formatting and migration-drift checks pass.

The setup exchange/loader, staged campaign, delivery checks, atomic
source/Family/configured-marker activation, final Compose demonstration and
three complete phase review/fix rounds remain open. These checkpoints do not
complete Phase 2 or authorize a merge.

### Ephemeral exchange and staged source execution checkpoint

The original setup attempt now queues one durable source-load Task. Its real
worker publishes an ephemeral public recipient; only the isolated ParishSoft
target can relay the sealed candidate to it. The recipient private key stays in
worker memory. Every provider request and staging batch repeats original-login,
candidate receipt, Task and source-fence admission. Provider calls hold no SQL
transaction. The load includes Ministry/fund catalogs without pretending that
an unconfigured financial window includes giving history.

An immutable result binds the validated ready snapshot to its exact exchange.
Task completion and returning the wizard to collecting commit together, with
source release before terminal Task state and the dispatcher completion signal
after commit. No current source, Family code or configured marker changes.
Invalid data is rejected; cancellation during a provider page stops further
requests and staging. The browser start action and closed worker/scheduler
registries use this owner. An initial worker may omit the installed ParishSoft
file only with coherent bootstrap-policy evidence; ordinary configured refresh
still requires its individual credential mount and working bytes.

The integrated loader/exchange/intake/progress run passes 62 PostgreSQL tests
with 91% focused coverage. Runtime assembly passes 13 tests. The baseline passes
3,827 tests with 2,231 explicit profile skips and two existing warnings. These
counts overlap earlier evidence and are not a full PostgreSQL or Compose rerun.
Abandoned source-row cleanup, initial Compose mount orchestration, staged first
campaign, provider delivery checks, final installer coordination and atomic
configuration/source/Family activation remain required before Phase 2 acceptance.

### Expired setup source disposal checkpoint

The scheduler now queues one idempotent cleanup Task for each expired original
setup that started a source load. The worker waits for source ownership and the
retained provider-request drain deadline, rejects only that attempt's unpromoted
manifests and removes at most 500 memberships per transaction. SQL restricts its
DELETE authority to those exact expired-setup rows. Payloads shared with another
snapshot survive; interrupted batches roll back both payload and membership
deletions. Immutable manifests and result/audit metadata remain. Original queued
loads are cancelled, while genuinely live old workers retain normal fenced
expiry/recovery rather than being impersonated.

The combined setup-disposal, loader, ordinary worker, snapshot and retention
regression passes 55 PostgreSQL tests in 1 minute, 46 seconds with 85% focused
cleanup coverage. One integration case waits for the actual finite provider
drain instead of changing clocks or disabling guards. Runtime composition passes
31 tests; the baseline passes 3,827 tests with 2,237 explicit profile skips and
two existing warnings. This closes the source-row disposal slice, not all of
ADM-02: staged campaign and final installer/activation integration remain open.

### First-campaign structural staging checkpoint

The original wizard can now save the first campaign's structural configuration
using only its validated unpublished Ministry/fund catalog. It shares the normal
campaign form's module, date, financial-period, fund and default share-option
validation, while keeping the initial timezone bound to the Parish profile.
An exact source-result binding and whole-attempt version protect both service
and SQL writes. Another login cannot read the catalog. Replacing the staged
source key invalidates the old catalog rather than silently using stale data.
Cancellation clears the staged campaign; no Campaign, source-current pointer or
configured marker is created by this form.

The combined first-campaign, public-draft and credential HTTP suite passes
27 PostgreSQL tests with 95% focused service/view coverage. The actual form
passes six Chromium/Firefox/WebKit mobile/desktop accessibility checks. The
baseline passes 3,827 tests with 2,250 explicit profile skips and two existing
warnings. Wizard-specific content, share-option and schedule editing, provider
test delivery and final installer/activation integration remain required.

### First-campaign content and schedule preparation checkpoint

The original wizard now has named page/email visual editors with sanitized
fictional previews, stable/reorderable financial share options and initial,
reminder, daily and weekly mail schedules. The normal editors share the same
field templates. Temporary content and schedules are bounded, validated against
the original source-bound campaign and scrubbed on cancellation. No Campaign,
delivery work or active configuration is created by these editors.

One attempt version now covers atomic related section saves. Replacing an email
revision updates its dependent draft schedules; clearing a referenced template
requires explicit schedule removal or replacement. Date edits cannot strand
mail. The combined editor can change campaign dates and correct every schedule
in the same transaction. Templates determine subjects, and the complete set
must obey initial/reminder ordering, unique mailing times and digest uniqueness.

The combined setup and ordinary-editor regression passes 73 PostgreSQL tests.
The focused wizard run passes 27 PostgreSQL tests with 96% coverage across its
new content/share/schedule service and view modules. Twenty-eight new pure
content-contract cases pass. Thirty-six selected browser cases pass across
Chromium, Firefox and WebKit, including mobile/desktop accessibility and the
visual editor's handling of executable source/paste markup. These browser counts
come from two nonoverlapping selections, not a full browser rerun. The baseline
passes 3,855 tests, with 2,285 explicit profile skips and two existing warnings.

Final setup preview, target delivery checks, installer/consumer coordination
and atomic configured-marker activation remain open. This is an implementation
checkpoint, not one of the three required complete Phase 2 review rounds.

### Exact public setup preview checkpoint

The original Admin can now review the complete proposed public configuration,
its digest, private staged branding and every enabled named content slot using
fictional samples. Compilation preserves bootstrap Admin records unchanged,
merges additional domain/address roles, retains campaign/content/share/schedule
identities and derives selected page references. Current sealed credential
metadata must match the draft's mailbox, sender, reply address, Testing recipient
and optional Slack channel. Disabled Slack/content staging remains inert.

A compact signed binding pins the original attempt revision, base, candidate
and request key. Verification recompiles under original-login/source/logo checks;
any edit makes the old preview stale. Passive preview reads do not extend idle
expiry or freeze setup. It exposes no completion action or provider-success
claim. The additive setup parser's record ceiling is increased from 300 to 400
so five allowed fifty-row access lists can coexist with 100 schedules and all
named content slots; canonical interpretation of previously valid intents is
unchanged.

The compiler/parser suite passes 50 pure cases with 99% compiler coverage.
The original-owner preview passes three PostgreSQL cases with 99% service/view
coverage; its combined cancellation/credential regression passes 40 cases.
Six additional browser accessibility cases pass across the three engines and
mobile/desktop widths. The baseline passes 3,879 tests, with 2,294 explicit
profile skips and two existing warnings. Provider delivery checks, initial
mount/consumer coordination and atomic source/Family/configured-marker
activation remain open; the finalizer must also cover the chosen financial
window rather than treating the earlier catalog load as giving-history evidence.

### Setup mail journal and private delivery checkpoint

The original Admin intake can create one explicitly requested fictional sample
bound to the signed preview, attempt revision, credential fingerprint and
Testing recipient. The request and its Task root commit together. Replayed
commands retain the same journal; concurrent pending tests are rejected. The
dedicated mail SQL role alone can record the submitting boundary, which must
commit before provider IO. Definitive acceptance/refusal and unknown results
are terminal. Another request after uncertainty requires explicit acknowledgement.

Bounded scheduler recovery cancels stale queued work but never resends it. An
in-flight lost result becomes unknown only after both the original Task claim
and helper deadline expire. Cancellation scrubs the fictional message without
claiming that an already submitted message was unsent. Every change generates
an immutable safe audit event containing identifiers, version and coded outcome,
not the recipient, rendered sample or credential. The raw SQL guards independently
enforce original login, current Admin, draft/key/recipient identity and worker
ownership. Migration downgrade refuses retained delivery history.

The internal provider adapter uses one fixed Workspace SMTP endpoint and one
Testing recipient. Its pipe helper has a finite total deadline, emits only a
closed outcome, and kills/reaps its child on timeout or lost ownership. Unknown
results never justify automatic resubmission. A successful DATA response is
retained even when the subsequent QUIT fails. Credentials and rendered content
are absent from process arguments, environment, temporary files and diagnostics.
The distinct ephemeral Workspace relay primitives bind each encrypted transfer
to the exact journal, credential revision and live mail-worker claim; a restarted
worker cannot recover another process's private recipient key.

The journal passes 12 PostgreSQL tests with 96% service coverage. The combined
delivery/helper/provider suite passes 120 pure tests with 96% coverage across
the new delivery modules. Ephemeral Workspace transfer passes 17 pure tests
with 100% module coverage. The combined 137-case pure run covers these modules
at 97%. The latest baseline passes 3,960 tests, with 2,306 explicit profile skips
and two existing warnings. A broader setup/grant run passes 201 cases before
finding an outdated empty-table DELETE expectation from the earlier cleanup
grant. After correcting that expectation, the focused populated-source cleanup
and actual-role regression passes all 33 cases. These
tests use only fake providers, synthetic credentials and disposable databases;
no real messages were sent.

The actual persisted Workspace relay, worker startup/consumer mounting, web
send controls, optional Slack delivery and atomic setup finalizer remain open.
This is an internal implementation checkpoint, not phase completion or one of
the three required complete Phase 2 review/fix rounds.

### Setup mail runtime and web integration

The actual Workspace installer relays its sealed candidate to the exact live
mail worker's ephemeral recipient. PostgreSQL enforces original draft/key/Task
bindings and one reply per recipient; replacement workers cannot recover the
private key. Cancellation scrubs relay ciphertext, and unrelated workers and
installers cannot read it. The Workspace target cannot read message bodies or
Family source data. No working credential file is installed by this relay.

The maintained mail handler commits its submission marker before invoking the
finite helper. Recovery derives completion from the retained journal and never
creates a retry. A failure before submission is retired by the scheduler even
when the setup attempt remains live, allowing a later explicit test. The actual
runtime admits mail dispatch with matching public/private key inventories and
allows a missing working Workspace file only in coherent bootstrap Testing
state. Consumer process receipts now support the single-process mail owner;
initial installation/mount recreation and SQL ACK integration remain open.

The original Admin can select a named fictional sample and explicitly request
delivery. Passive GET status polling does not extend login idle expiry. An
uncertain result requires visible acknowledgement before another request. Stale,
denied or mismatched status disables sending until reload. Real HTTP checks
cover CSRF, duplicate/unknown fields, exact preview invalidation, other-login
denial, replay and cancellation. The SQL-role fixture preserves the restricted
web login after error middleware closes a connection.

The combined mail journal/relay/Task/HTTP suite passes 41 cases with 95% focused
coverage: journal 96%, relay 95%, handler 93% and views 100%. The composed test
uses real encryption, SQL roles and maintained execution, substituting only the
external provider call. All 15 browser behavior cases and six responsive/axe
checks pass across Chromium, Firefox and WebKit. The runtime/process/consumer
suite passes 111 cases. The baseline before the last five added composition/
recovery cases passes 3,971 tests with 2,348 explicit profile skips and two
existing warnings. No real provider credentials or outbound messages are used.

Optional Slack notification, final credential installation/ACK coordination,
selected financial-window loading and atomic configured-marker activation still
prevent Phase 2 acceptance. This is not a completed full-phase review round.

### Optional Slack readiness integration

An original Admin can explicitly request a fixed fictional notification after
reviewing the exact setup preview. The immutable request binds the current
attempt revision, configuration digest and staged Slack credential revision.
Its target-specific installer consumes a separate durable metadata queue, so
the web and general worker never receive its private token. A SQL-owned
submitting marker commits before provider IO. Terminal results cannot be replayed
or rewritten; another request after uncertainty needs explicit acknowledgement.
Changing the channel invalidates queued work rather than silently retargeting it.

The private helper issues one bounded, fixed-endpoint `chat.postMessage` request
with a fixed Testing message, no Family data, no mentions and no link previews.
It follows the [official method contract](https://docs.slack.dev/reference/methods/chat.postMessage/).
Only exact acceptance or documented explicit refusal produces a definite result;
unknown errors and missing/malformed responses remain uncertain. The mail and
Slack helpers share their finite, non-retrying pipe transport. Secrets are absent
from process arguments, environment and diagnostics. The existing authentication-
only connectivity helper still cannot send notifications.

The original-login web page reuses passive bounded status polling. CSRF,
unexpected/duplicate fields, signature changes, original-login isolation and
server-side resend acknowledgement are covered by real HTTP tests. Scheduler
and isolated-target recovery classify stale queued work and expired uncertainty
without sending a message or exposing a completion control.

The combined Slack/source-relay/mail-relay PostgreSQL regression passes 38 cases,
with 94% focused coverage (Slack service 93%, views 96%). The combined mail/Slack
HTTP run passes eight cases with 98% view coverage. The provider/private-helper
suite passes 85 cases with 93% focused coverage. All 36 selected browser behavior
and responsive/axe checks pass across three engines; the runtime/provider suite
passes 196 cases. The full baseline passes 4,025 cases with 2,389 explicit profile
skips and two existing warnings. Ruff, formatting, migration drift and whitespace
checks pass. All inputs/providers remain synthetic; no real Slack notification
or email was sent. Initial installation/consumer ACKs, selected financial-window
coverage and atomic configured-marker finalization remain open.

### Exact confirmation intake

The internal original-Admin confirmation service now commits the exact v7
configuration request, frozen attempt and immutable readiness binding together.
It requires accepted mail, accepted Slack when enabled, no pending tests and
the original validated catalog. Readiness binds the reviewed revision/digest,
current credential revisions and Testing recipient; it does not invent selected
financial coverage. PostgreSQL independently checks the original live Admin,
frozen request, source identity and accepted delivery receipts.

Replayed confirmation returns the same original receipt without renewing login
idle expiry. A failed final binding insert rolls back the request and freeze.
Changed public settings invalidate prior readiness even when the credential
itself still works. No authority file, configured marker, source pointer or
Family code is activated here, and the configuration activation guard remains
closed. The service is deliberately internal until the complete initial
installation/consumer/final-activation workflow can consume it.

Ten PostgreSQL tests pass with 98% service coverage. The earlier eight-case
confirmation run combined with existing setup cancellation/abort tests passes
25 cases. Ruff, formatting and migration drift checks pass. This checkpoint
does not complete ADM-02, expose a product completion control, or count as a
complete Phase 2 review round.

### Initial credential installation and rollback retention

Each isolated installer now atomically binds its own frozen setup credential
to a normal installation request, preserving the sealed envelope's original
namespace and the original Admin authentication time. PostgreSQL verifies the
exact readiness input and ownership; failed intake leaves no partial request.
Private bytes stay within the target installer. Other targets cannot read the
copied ciphertext, and historical bindings cannot be rewritten.

Consumer acknowledgement is an initial-readiness barrier, not permission to
discard rollback files. Both the installer and SQL retain the predecessor until
the configured marker exists. Original-attempt cancellation restores the old
credential or removes a wizard-only new file. Ordinary replacement semantics
remain unchanged. The maintained target loops consume this intake; mail-dispatch
now supports the existing whole-service acknowledgement command and restricted
SQL acknowledgement grants. No process gains Docker control.

The combined initial-installation and ordinary credential-isolation PostgreSQL
suite passes 22 cases, with 92% coverage of the new service. Runtime and
acknowledgement tests pass 53 cases. Ruff, formatting, migration drift and
whitespace checks pass. The full baseline passes 4,031 tests with 2,408 explicit
profile skips and two existing warnings. Configuration preparation, initial consumer
mount/recreation integration, selected financial coverage and atomic activation
remain open; no whole ADM-02 task or full-phase review round is complete here.

### Initial YAML preparation and safe finalization handoff

The isolated configuration installer now consumes the frozen v7 request only
after exact target installation and complete consumer acknowledgement. It
prepares the immutable public projections, selects the exact YAML and stops at
`yaml_activated`; it cannot commit database activation. Repeated passes retain
the same receipt, including recovery after a real post-selection crash.

An immutable preparation receipt provides the subsequent finalization worker
with safe handoff evidence. Its SQL insert requires the real configuration
installer, its pinned installation lock, original live setup, exact selected
candidate checkpoint and every initial credential's consumer acknowledgement.
The installer gains only the original session's liveness columns, never its
browser credential. A separate child credential cancellation cannot invalidate
this proof while its original setup remains live.

Original-attempt cancellation still uses the approved selected-but-unapplied
abort journal. Cancellation before public preparation now terminates the queue
item without pretending to restore a manifest, allowing a subsequent attempt.

The combined YAML preparation, setup abort, configuration isolation and
credential regression passes 59 PostgreSQL cases with 87% new-service coverage.
Two additional SQL cases reject child rollback outside original setup
cancellation. The runtime-grant/database unit suite passes 27 tests. The full
baseline passes 4,031 tests with 2,419 explicit profile skips and two existing
warnings. Ruff, formatting and migration drift checks pass. Actual initial
consumer mounting/recreation, selected financial coverage and atomic source/
Family/configuration completion remain open; this is not a completed phase
review round or a complete ADM-02 workflow.

### Initial background runtime and provider mount variants

Provisioning now emits complete initial, configured-without-Slack and
configured-with-Slack Compose selections using the same service identities and
durable storage. Initial worker/mail profiles omit files that the wizard has
not installed. Mail dispatch has its own SQL/Valkey identity and budget;
only the three provider targets gain outbound validation access. The
[runtime guide](stewardship-runtime.md#first-database-and-application-startup)
documents whole-service recreation and actual-consumer acknowledgement.

Live startup uncovered and corrected pre-Django model imports in background
assembly and process startup. Fresh-process regressions now prevent pytest's
preconfigured registry from hiding that failure. Mail admission verifies the
exact public YAML/SQL document without loading unused parish projections or
expanding its restricted SQL privileges.

All 134 focused runtime/provisioning tests pass. Both rebuilt-image initial
Compose scenarios pass, covering development and production-shaped startup,
actual worker/scheduler/mail liveness, private mount selection and existing
foundation isolation checks. The full baseline passes 4,047 tests with 2,421
explicit profile skips and two existing warnings. Ruff and formatting pass.
The atomic finalization owner, real initial provider-install/recreation/ACK
demonstration and full Phase 2 review gate remain open.

### Exact final-setup source coverage

An internal finalization Task now binds to the immutable installer preparation
receipt, original frozen Admin and exact selected public YAML. Its fresh full
load uses the reviewed campaign's selected financial periods and funds. Every
page and staging boundary repeats original-login admission, mounted-key receipt
and Task/source ownership. The earlier catalog snapshot remains unchanged;
neither this new ready snapshot nor its Task implies configured product state.
The finalization Task remains outside the operational registry until its atomic
completion owner is connected.

Seven restricted-role PostgreSQL cases pass with 87% coverage across the new
scope, task and loader modules. Four additional cases pass for stale/changed
manifests and original cancellation during a provider page. Both loads respect
the real previous HTTP drainage deadline. Twenty-two pure recovery cases pass;
the combined grant/preparation/recovery regression passes 59 cases. The baseline
passes 4,069 tests with 2,432 explicit profile skips and two existing warnings.
Ruff and formatting pass. Final source-staging cleanup, atomic activation,
configured-marker routing and the end-to-end demonstration remain open.

### Cancelled final-load disposal

Expired setup cleanup now includes the exact prepared finalization retry root
as well as the original catalog root. Both the queryset and PostgreSQL deletion
guard verify the preparation/attempt/initiator binding; neither includes a
promoted source or unrelated staging. Cleanup fences an expired final worker,
cancels its drained Task and removes temporary payloads while retaining safe
request, preparation and source-manifest history. Populated final-task history
prevents downgrading away its only cleanup owner.

Both real queued/staged finalization cancellation scenarios pass, including
actual worker/source/HTTP lease drainage, manifest restoration and restricted
scheduler/cleanup execution. The surrounding source/disposal regression passes
24 additional cases, including guard reversal/reapplication and shared-payload
preservation. The baseline passes 4,069 tests with 2,436 explicit profile skips
and two existing warnings. Ruff, formatting and migration drift checks pass.
Successful completion cleanup and atomic source/Family/configured-marker
activation remain open; this is not a Phase 2 review-gate completion.

### Atomic initial completion and configured status

The restricted worker can now atomically promote the exact fresh final source,
activate the prepared configuration, create the first Testing draft and Family
codes, record immutable completion, scrub temporary public/private setup inputs,
and succeed its Task. Deferred SQL constraints reject source/configuration
without that terminal outcome. The completion guard compares the actual source
Family set and eligibility with the derived population, verifies selected
financial coverage, and rejects promotion of the earlier catalog. The worker
never writes YAML or prepared configuration projections. Its additional writes
are limited by initial-owner guards and column grants; campaign state, ordinary
credential changes, and private credential-body reads remain unavailable.

The web runtime now reads the immutable completion marker on each configured
status check. Prepared YAML alone remains unconfigured. Marker provenance does
not pin subsequent requests to the initial configuration or source generation.
Empty-history guard reversal/reapplication is covered; completed history refuses
the downgrade. The original Testing recipient changes only through this exact
initial activation's dedicated guard.

Twelve PostgreSQL completion tests pass with 96% focused coverage. They cover
census-only and financial campaigns, actual web/worker grants, cross-target
temporary-input scrubbing, failed effects and same-owner retry, omitted Task or
population effects, and migration reversal. After strengthening source/population
SQL checks, these tests pass again alongside four restart-hold tests (16 total).
The surrounding grants, preparation and storage regression passes 77 tests.
The baseline passes 4,069 tests with 2,448 profile skips and two existing warnings;
Ruff, formatting and migration drift checks pass.

Operational finalization scheduling/consumption, restart recovery, final web
confirmation/status, unused catalog cleanup after success, and the complete
initial Compose demonstration remain in progress. This is an internal checkpoint,
not completion of ADM-02, Phase 2, or a full-phase review round.

### Compiled setup finalization and public confirmation

The actual scheduler now produces the immutable prepared setup's final Task,
and the compiled restricted worker owns fresh source loading and atomic
completion. Exact selected-but-unapplied startup admission keeps web, scheduler
and mail recovery available without admitting ordinary mismatched work. The
scheduler expires abandoned original logins before attempting finalization.
Successful cleanup removes the unused catalog through the real scheduler and
worker, retaining current/shared payloads, immutable receipts and the configured
marker. Existing cancelled-final-load disposal remains intact.

The setup preview now links to an explicit readiness-checked confirmation form.
Its original-login progress page shows credential/checkpoint/Task metadata,
explains actual-consumer recreation and acknowledgement, and never renews idle
time. Completion returns a currently authorized Admin to the main portal;
anonymous requests and completion races cannot bypass current authorization.

Ten combined PostgreSQL tests pass with 93% focused coverage across confirmation,
progress and final scheduler/worker execution. These exercise successful
completion and cleanup, credential retry, invalid-source failure, real web
grants, CSRF, exact confirmation and passive progress. Eight additional
cleanup/restart regressions pass. Twenty-seven browser checks pass across
Chromium, Firefox and WebKit, including both responsive widths, automated WCAG
checks, explicit acknowledgement and absence of persisted browser drafts.
The rebuilt image passes both initial-startup Compose scenarios. The complete
baseline passes 4,082 tests with 2,487 explicit profile skips and two existing
warnings. Ruff, formatting, migration drift and focused Markdown checks pass.

The complete initial installation/recreation/ACK Compose demonstration,
post-setup campaign readiness-test email and integrated phase acceptance remain
open. This internal checkpoint does not count as a complete phase review round.

### Applied campaign readiness-test mail

Applied email revisions now have an explicit fictional preview/send screen that
routes only to the configured Testing recipient. Signed previews pin current
configuration, campaign, template, Admin and installed Workspace identity.
Duplicate commands retain one journal/Task root; changed configuration, revoked
Admin access, restore or cleanup gates cannot initiate new effects. This Phase 2
consumer admits the sole Testing draft even before its campaign interval; it
does not enable Production/restore delivery or fulfill a scheduled occurrence.
Those later owners must explicitly extend their admission and use the retained
exact-input test result as readiness evidence.

The actual installed mail consumer commits `submitting` before invoking the
existing finite private delivery helper. SQL fixes routing and immutable input
bindings, fences the one allowed submission, preserves late/lost uncertainty,
scrubs terminal message content and retains safe audit/outcome history. Scheduler
recovery waits for both helper and Task deadlines. Neither an abandoned Task nor
an explicit Task retry can reenter submission; a new test after uncertainty
requires a new command and explicit acknowledgement. Failed helper drainage
leaves the original checkpoint for recovery rather than claiming a safe outcome.

Thirty-two focused tests pass with 92% coverage. They cover actual web/mail/
scheduler permissions, exact/expired/forged input, replay, all provider outcomes,
changed configuration/credential/Admin authority, cleanup gates, SQL recipient
substitution with atomic Task rollback, migration reversal and populated-history
refusal. A fatal-drain case waits for the actual lease/deadline before recovering
uncertainty without resending. Twenty-one browser checks pass across all three
engines, including responsive WCAG checks, passive previews, uncertain/pending
states and browser-local timestamps. Runtime assembly/process/grant tests pass
73 cases. The baseline passes 4,097 tests with 2,525 explicit profile skips and
two existing warnings; Ruff, formatting and migration drift checks pass.
The surrounding real-auth grants, setup restart/mail and content-editing
regression passes 28 PostgreSQL cases.

The complete initial installation/recreation/ACK demonstration and integrated
Phase 2 acceptance/review gate remain open; this is an internal checkpoint.

## Container timezone catalog correction

The full initial-setup Compose demonstration exposed a packaging omission:
the strict image build context excluded the frozen timezone-name schema asset.
The first parish form therefore failed in the image even though host tests and
the separately tested wheel included the catalog. Both synchronized ignore
files now admit that exact asset, without admitting arbitrary text files.

The two real scratch-build context tests prove inclusion and adjacent private
text exclusion. Ninety-one packaging/schema tests pass. The rebuilt development
image contains the catalog; the complete setup demonstration is still running.
This is a discovered integration correction, not a completed phase review round.

## Integration-selection migration fixture correction

The first complete PostgreSQL coverage run reached 942 passing cases and one
failure before being stopped for correction. The credential-selection downgrade/
upgrade test recreated the later initial-installation binding table without
restoring its target-installer SELECT grant. Its subsequent ordinary credential
completion correctly failed under the restricted role.

The test now reapplies that exact grant after DDL, matching the existing runtime
upgrade sequence. It does not weaken production grants or make migrations grant
runtime authority implicitly. All 14 integration-selection PostgreSQL cases
pass. The full coverage rerun remains in progress; this is not a review round.

## Complete initial runtime demonstration

Both development and production-profile complete initial-setup demonstrations
pass against the rebuilt image. They start without provider files, use the
original Admin's real authenticated/CSRF-protected forms, execute staged source
and readiness delivery work, freeze configuration, recreate the actual consumer
services and obtain whole-service ACKs. The final worker loads the selected
financial periods and atomically commits the configured Testing draft, source
and Family population. The original progress page returns to ordinary Admin
navigation only after that transaction.

The strengthened full operational suite passes all six cases in 489.90 seconds.
Read-only operator SQL verifies complete financial source, eligible Family codes,
population/current-source agreement, scrubbed private/public staging and a saved
invitation schedule with no live occurrences or fulfillment. The web's narrower
source grants are unchanged. Normal provisioning already retains the configured
service-file variants; the fixture now stages those variants before creating its
native volume. External provider responses remain synthetic, as detailed in the
[acceptance index](stewardship-phase-2-acceptance.md).

All 447 browser cases pass in 476.93 seconds. Baseline coverage passes 4,097
tests with 2,527 explicit profile skips and two existing warnings. The full
PostgreSQL coverage rerun and remaining container checks are in progress.
Ruff, formatting and Markdown checks pass. M2.01 is complete; full Phase 2
acceptance and three independent complete-phase review/fix rounds remain open.

## First complete-phase review

The independent full-phase review of `0b9677f` completed successfully with both
models. Its 36 validated Medium findings and ongoing correction evidence are
tracked in the [round 1 disposition record](stewardship-phase-2-full-review-1.md).
No High or Critical findings were returned. The correction pass and integrated
validation still prevent phase exit; two further full rounds remain required.

## Second complete-phase review

Both vendors completed the review of `58e7a8c` with 23 retained Medium findings
and no High/Critical findings. The
[round 2 disposition record](stewardship-phase-2-full-review-2.md) tracks
corrections and validation, including the subsequent clean 2,091-case full
PostgreSQL coverage run. The third review below supersedes the earlier pending
review status; final post-correction integrated acceptance is still required.

## Third complete-phase review

Both vendors completed the review of `0352749` with 39 retained Medium findings
and no High/Critical findings. All 40 generated Claude shards and the single
pika-owned Codex reviewer completed without degradation, failed agents or verdict
mismatch. The [round 3 disposition record](stewardship-phase-2-full-review-3.md)
records all findings, corrections, evidence-backed pushbacks and regression
evidence. Corrections are committed through `ea2d5cb` and pushed to the topic
branch. Final integrated validation passes: all 2,114 PostgreSQL cases and the
baseline/browser/container/operational profiles, with 94.16% line and 85.04%
branch coverage. The three-round exit criterion is satisfied. PR CI and human
merge approval remain required.
