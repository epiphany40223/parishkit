# Phase 1C review ledger

This ledger follows the [automated delivery cycle](../plans/stewardship/overall.md#automated-phase-delivery-cycle)
and [Phase 1C scope](stewardship-phase-1c.md). Review outputs remain private local
artifacts, not repository content. Review completion does not release Gate 1;
human approval remains required before Phase 2.

## Round one

Reviewed implementation: `abf407720a5ccf52ead21813381dbe08b8996aed`.
Base: PR #20 merge `18a37cb5b6c90bbf2b5f60c5fff37f199cd52201`.
Local session: `20260910-225925-d0301e`.
Five Pika-generated Claude shards and Pika's single Codex reviewer completed;
finalization has no failed agents, degradation or verdict mismatch. The slowest
Claude shard completed in approximately 32 minutes, within the approved window.

Raw severities: 3 High, 22 Medium, 62 Low. Pika's Medium cutoff retained
25 findings: 21 Claude-only and 4 Codex-only. The Low observations were not
retained as actionable findings. The result is **request changes**, not approval.

Corrections and post-fix validation are complete for this round: 23 findings
corrected, one duplicate and one unsupported premise rejected. The following
dispositions record the evidence-backed technical decisions.
Identifiers preserve each vendor's finalized order, including duplicates.

| ID | Severity | Finding | Disposition |
| --- | --- | --- | --- |
| C1 | High | Recovery preview indexes roles on Ministry assignments | Corrected: restrict Admin listing to address rules; assignment records now exercise every preview case. |
| C2 | Medium | Database-provision missing from offline mount matrix | Corrected: actual distinct operator password and all-read-only mount matrix. |
| C3 | Medium | Response helper ignores operational download pool | Corrected: resolve the startup-admitted pool; no ordinary-web-login fallback; missing admission returns 503 before private reads. |
| C4 | Medium | Migration safety branches lack focused evidence | Added direct refusal tests and real Compose configured-upgrade hold plus unchanged-capacity retry verification. |
| C5 | Medium | Operator profile missing from topology assertions | Corrected: database-provision explicitly remains profile-gated with no restart policy. |
| C6 | Medium | Runtime SQL connection cap not compared with process budget | Corrected: online/offline/migration admission checks actual role cap. Health-thread headroom is explicitly budgeted. |
| C7 | Medium | Closed grant registry lacks fast tests | Added model/SQL-table inventory, supported/unsupported identities, narrow download, role-cap and column-grant tests. |
| C8 | Medium | Failed metrics reuse query can retain valid=True | Corrected: every validation exception leaves the candidate invalid; PostgreSQL regression proves no replacement after a transient lookup failure. |
| C9 | Medium | Renderer admits unsupported replicas | Corrected: renderer, provisioner and cohort confirmation all require one operational web container. |
| C10 | Medium | Budget admits timeouts rejected by read limits | Corrected: validate fixed lock/interactive floors before provisioning; boundary regressions added. |
| C11 | Medium | Additive grants silently retain obsolete broader access | Corrected: inspect existing table/column grants and refuse excess; never revoke or repair unrelated authority automatically. |
| C12 | Medium | Bootstrap RLS visibility lacks non-superuser proof | Added actual schema-owner visibility and bootstrap rejection with foreign credential history in a disposable container, plus unknown-RLS refusal. |
| C13 | Medium | Mutable SQL password-reference map | Corrected: copy into a read-only mapping, including dataclass replacements; serializer avoids deep-copying mapping proxies. Hashability is not claimed: other existing mapping fields were already unhashable. |
| C14 | Medium | Download role inherits unrelated web reads | Corrected: separate closed streaming/authorization registry excludes credential/link/audit/queue inventories. Later report owners add their data explicitly. |
| C15 | Medium | Service configuration can overlap writable stores | Corrected: validate path/aliases/containment, with only the intended service-metadata directory exception. |
| C16 | Medium | Partial initial provisioning marker strands retry | Corrected: lock before writing; resume an exact empty/prefix intent only when no side effects exist; fsync completion before creating other artifacts. |
| C17 | Medium | Monitoring can exhaust request threads | Corrected: bounded single-flight observations with short cache/deadline and SQL statement/lock timeouts; at most two observation threads per process, included in connection budgets. |
| C18 | Medium | Proxy lease refusal lacks diagnosis | Corrected diagnostic. Retain immediate refusal: a maintenance exclusion must not quietly turn into delayed ingress activation. Production restart behavior is handled by X1. |
| C19 | Medium | Credential override can overlap Valkey ACL/shared parent | Corrected reserved ACL directory; dedicated owner-only closed parent inventory addresses shared authority. Independent-root overrides remain supported as required, rather than imposing the suggested root-only restriction. |
| C20 | Medium | Alternate migration owner can hide RLS rows | Rejected as unsupported premise: operational migration requires exact current/session identity pk_stewardship_migration, and role names are not configurable. Actual non-superuser owner/RLS regression verifies the supported path. Restore ownership remains OPS-06, not an alternate bootstrap entry point. |
| C21 | Medium | Long-lived production services do not restart | Duplicate of X1; same correction and evidence. |
| X1 | High | Production crash/daemon recovery missing | Corrected: long-running production services use unless-stopped; offline commands retain no restart. Added actual synthetic web-supervisor crash/restart proof. |
| X2 | High | File override lends installer its shared parent | Corrected with dedicated private-directory and closed protocol-file admission during rendering and actual online startup. Symlinks, hardlinks, subdirectories and unrelated files are refused. |
| X3 | Medium | Provisioning retry adopts unexpected storage | Corrected: revalidate the complete closed destination inventory on every retry, preserving/refusing injected files. |
| X4 | Medium | Installers/Valkey/Caddy lack health checks | Corrected: PID/start-time/recent-loop heartbeat, authenticated-default-denial broker liveness, and bounded local proxy socket checks. These are liveness, not external-provider readiness. |

Docker restart policy and health-check behavior follow the
[Compose service reference](https://docs.docker.com/reference/compose-file/services/).
No Docker daemon restart is performed on the developer's shared environment.

Post-fix evidence: 2,623 baseline tests and 940 PostgreSQL tests pass; scoped
coverage is 91.24% lines and 82.75% branches. The rebuilt image passes all 47
combined operational Compose, native provisioning, existing Compose and isolation
checks, including synthetic web-supervisor crash/restart and restricted-owner RLS
visibility. All 66 browser checks pass. Ruff, formatting, Markdown, migration drift
and diff whitespace checks pass. This completes round one, not the three-round
exit or Gate 1. Image digest: `sha256:7ba3f6008efc9d73d754928ef40ab1afe6b73e66d403c409a4eab2fd9e34b632`.

## Integrated Gate 1 review scope

Rounds two and three must review the cumulative Phase 1 diff from Phase 0's
merged handoff `509245d997c9b278f47b5c7630894ddc7b38650b`, including already
merged Phase 1A/1B work. Round one alone is not the complete integrated gate
review. The gate remains pending until those reviews, corrections, demonstrations
and final validation pass and the human approves release.

## Round two: integrated review corrections

Reviewed implementation: `15a82b0985212112f00cb9b8a6519086d9808828`.
The cumulative base is the Phase 0 handoff above. Session
`20260910-235922-283abd` completed all 31 Pika-generated Claude shards and the
single Pika-launched Codex reviewer in approximately 18 minutes, with no failed
agents, degradation, verdict mismatch or salvage requirement. Raw severity
counts are 3 High, 53 Medium and 329 Low; the severity/confidence cutoff retained
55 findings (54 Claude-only, one Codex-only). The result requests changes.

The unchanged reviewed commit passes 2,623 baseline tests, 940 PostgreSQL tests,
48 real-container/Compose checks and 66 browser checks. Scoped coverage is
91.29% lines and 83.40% branches. Ruff, format, Markdown and migration checks pass.
These are pre-correction results, not round-two completion evidence.

Correction checkpoints, with identifiers retaining finalized vendor order:

- C1/C8: translate the operator OAuth document into allauth's actual secret
  field; add library-consumer and composed-runtime probes.
- C2: add the exact missing EXISTS/DEL ACL commands; the composed probe exercises
  limiter operations and rejects unrelated keys/administrative commands.
- C3/C14: exercise restricted-role activation with a current campaign/schedule;
  add boundary UPDATE and TaskRun SELECT required by its invoker triggers.
  Exceptional reopen remains refused without its later ADM-06 owner; unrelated
  future transition authority is not granted preemptively.
- Related restricted-web tests expose and correct missing Family epoch/token
  row-lock and activity/dirty-trigger column privileges. Code/token login and
  the authenticated page pass with the actual closed web registry.
- C4/C5/C10: strengthen same-registry excess-grant tests, real diagnostics
  lease/admission/cleanup branches and production Gunicorn option coverage.
- C6: ordinary configured routes skip duplicate gate authentication and leave
  expiry/revocation/rotation to their owning view. Setup/maintenance routes keep
  full authentication because their placeholders have no independent identity
  owner. A read-only precheck was tested and rejected: it incorrectly prevented
  expired-session revocation and privilege-change cookie rotation.
- C9/C28: malformed query input receives Invalid request; server-side formatting
  failures receive private retry responses and failed terminal audit evidence.
- C11/C15: multipart activity input is a 400; Family exchange reuses only the
  configuration scope freshly resolved under its credential-population locks.
- C13: invalidate and clean a real testing Family session and its protected
  Django parent, checking the independent bounded cleanup counts.
- C16/C36: current source-promotion attribution reaches eligibility history;
  callers cannot supply the allocator's history-suppression placeholder.
- C17: give the invalidated-rehearsal retention exception a distinct forward
  migration identity and an explicit conformance-test exception.
- C18: login/grant resolution shares normalization and rejects ignored targets.
- C19/C29: historical reverse-function rebuilds restate trusted search paths;
  partial-downgrade regressions check the complete retained pin inventory.
- C20: the online queue excludes interrupted offline-owned recovery intents.
- C25/C26: session-disappearance races and non-owning sealed-request expiry fail
  with their typed denial before durable mutation.
- C27: response teardown failure still records exactly one failed terminal audit.
- C31: failed post-commit counter cleanup cannot discard the new session cookie.
- C33: protected diagnostics report ordinary offline maintenance distinctly.
- C23/C49: bound both durable observation/incident lock waits. Keep cross-worker
  recovery observation: a worker that did not see the original outage must
  still resolve its durable incident after recovery.
- X1: add the new synthetic runtime modules to CI and release validation;
  the exact validation command fails on any skip. Final validation remains pending.

Remaining finalized findings and dispositions:

- C7/C40/C45: lock only mutable campaign rows, explicitly pin runtime/epoch
  before campaign/generation inputs, and retain the consistent restore/lifecycle
  order. Real independent-connection NOWAIT tests cover preparation, verification
  and cancellation; the actual web role exercises campaign/projection reads.
- C12: retain the later BG-02 token-issuance owner. Web deliberately has no token
  INSERT authority, so granting it the suggested issuance-trigger privileges
  would not authorize that workflow and would expand its scope. Corrected the
  actual code/token-login row-lock and activity grants instead, with real-role
  HTTP coverage; issuance keeps its owning-service admission requirement.
- C21/C22: assert exact Linux proc/PID identity and hyphenated service override
  names in regression fixtures.
- C24: fetch full canonical history/projections in batches of 64; keep complete
  lineage verification, compact ancestry metadata and parsing outside the writer
  lock. Query-growth tests span three batches, without a trust watermark or
  arbitrary history cutoff.
- C30: cancellation has a transaction-bound admission callback, rechecked even
  on exact retries. False/absent confirmation from a supplied callback denies.
- C32/C42: forward SQL guards reject stale sealed-request authentication at
  intake and refuse sealed payloads attached to legacy empty-consumer requests.
- C34: selected installer failures carry their durable request correlation and
  closed diagnostic category; no exception text, path or provider value is logged.
- C35: real Chromium/Firefox/WebKit tests verify CSP blocks unrelated form
  destinations. The test must use a function rather than string evaluation to
  avoid violating the policy itself.
- C37: competing restore decisions return a typed stale conflict; identical
  retry intent still returns its original receipt without new evidence.
- C38: omit unknown dependency metrics while readiness continues to fail closed.
- C39: reject regressing pause/resume timestamps in SQL. Reject the suggested
  campaign-start lower bound: pre-start scheduled Production pauses are expressly
  supported and already tested.
- C41/C43: reject false premises, proven by actual production-shaped Compose
  tests: runtime settings already admitted loopback probes, and pinned Caddy
  redirects to the standard public HTTPS port, not its internal 8443 listener.
  Docker restart policy is not a health-triggered restart mechanism.
- C44: reject after the existing real-SQL bootstrap regression disproved the
  premise. Initial singleton INSERT is empty, then atomic activation UPDATE
  creates the campaign and selects its pointer. A complete legacy root therefore
  cannot leave the alleged dangling projection. Operational bootstrap still
  generates only its minimal login-rule document. The proposed extra restriction
  was removed rather than breaking the supported complete-root storage path.
- C46: preserve pinned Caddy's default untrusted-client XFF normalization;
  add forged multi-hop XFF to the real TLS ingress regression rather than
  changing the application to trust arbitrary chains.
- C47: a metrics receipt-reuse SQL outage remains retryable in Testing state,
  preserves the prior file and cannot be mistaken for candidate invalidity.
- C48: document the intentionally isolated, fixed container-tmpfs/PID namespace.
  Reject the unsupported shared-host-process premise; these transient evidence
  files are not durable deployment-root stores and must never be host-shared.
- C50/C51: refuse unverified external SQL connections and restrict loopback Host
  exceptions to admitted internal probes. Canonical application origin remains
  mandatory; private Compose SQL and isolated literal-loopback tests are supported.
- C52/C54: owning-service admission errors and restore-held boundaries remain
  retryable instead of terminally rejecting intent or reporting storage corruption.
- C53: reject the proposed success prerequisite for coalesced coverage. The
  background-processing/data specs require transactional coverage when assigning
  a replacement, preventing duplicate rearming across schedule revisions. That
  receipt is not provider success; dependent-resolution consumers still inspect
  the replacement outcome under their later owning task contracts.

Initial correction runs pass 46 focused runtime tests, 35 grant/limiter-contract
tests, and 97 PostgreSQL authentication/response/recovery/incident cases.
Further findings, migration tests, composed validation and the complete
post-correction suite remain in progress. This does not complete round two or
the Gate 1 review cycle.

Final correction checkpoint: all 55 retained findings have dispositions above;
none require a new product decision. The complete regression run exposed the
C6 session-precheck regression, C9 HTML-error compatibility, C34 SQL event
vocabulary and C44 false premise. Those are corrected; all 192 affected
PostgreSQL cases now pass. The latest baseline passes 2,658 tests; all 69 browser
checks and two complete 48-check container runs pass. The final rebuilt image
and complete PostgreSQL/coverage repeat remain required, and run alongside the
third independent review. No gate release is implied by these corrections.

## Round three: cumulative Gate 1 review

Reviewed implementation: `f6948c08eb856bac34a5cf01ac6d5e26970af447`.
The cumulative base remains the Phase 0 handoff above. Session
`20260911-070626-811662` completed all 32 Pika-generated Claude shards and the
single Pika-launched Codex reviewer in approximately 18 minutes. Finalization
reports no failed agents, degradation, verdict mismatch or salvage requirement.
The earlier session-limit failure did not count as a completed review round.

Raw severities: zero Critical/High, 58 Medium and 267 Low. Finalization retained
57 Medium findings: one agreed and 56 Claude-only. Low observations were below
the retained action cutoff; they do not imply deferred accepted Medium findings.
The final review action is COMMENT, not an assertion that corrections or Gate 1
approval were already complete. Round-two final validation subsequently passed
2,658 baseline tests, 976 PostgreSQL tests, all 48 rebuilt-container tests and
69 browser tests, with 91.87% line and 83.68% branch coverage.

The following dispositions use finalized vendor order. They follow the owner's
delegated, specification-consistent correction policy; none changes a product
decision or grants authority to deploy, merge or use live providers.

| ID | Finding | Disposition |
| --- | --- | --- |
| A1 | Owner-admission retry leaves installer head validating | Corrected the empty-string failure-code check; a refused preflight leaves the request staged and retryable before checkpoint publication. PostgreSQL exceptional-end regression verifies a successful later retry. |
| C1 | Internal lifecycle helper names start with underscores | Skipped, negligible impact: the module expressly defines internal lifecycle storage, not a public API. Internal imports do not make these stable external contracts; renaming seven callers adds no behavioral protection. |
| C2 | Database test guide omits Valkey | Added pinned disposable Valkey startup, authenticated readiness and teardown alongside PostgreSQL. |
| C3 | Nullable model fields fail full validation | Exclude only actual None values of nullable fields from blank validation; retain model cleaning, non-null validation and SQL constraints. Added nullable and invalid-UUID regressions. |
| C4 | ARC-02 evidence still claims runtime integration missing | Updated task evidence and the standalone production-settings refusal to point to the actual admitted runtime command. |
| C5 | Retained rehearsal ciphertexts lack rotation coverage | Added real PostgreSQL general/token rotation counts, plaintext preservation and complete migration verification. |
| C6 | Raster normalization tests omit formats | Added RGB/CMYK JPEG, WebP, EXIF orientation, metadata stripping and real animated APNG/WebP refusal tests. |
| C7 | Hardlink refusal fixture fails earlier on mode | Set the linked inode to 0600 so the test exercises the hardlink rule. |
| C8 | Mount-policy branches lack focused tests | Added media read-only admission and writable/foreign-password refusal across web, worker and backup identities. |
| C9 | Request guide says all requests use v1 | Document current schema selection and stored-schema dispatch on historical retries. |
| C10 | Offline refusal dispatch covers only half the commands | Parametrize all six operator commands. |
| C11 | Browser CI can silently skip acceptance | Require no skips in CI and release browser jobs; assert workflow parity. |
| C12 | CI can skip browser/isolation checks | Duplicate of C11/C20. |
| C13 | Request guide says policy/campaign schemas are unavailable | Link the implemented policy/campaign owners; preserve the later content-schema boundary. |
| C14 | OPS-02 prose contradicts completed task boxes | Replace obsolete missing-work prose with current runtime and emitter evidence. |
| C15 | Offline mount admission lacks positive infrastructure test | Add standard read-only system/source, process and tmpfs mount admission fixtures. |
| C16 | Read deadline does not prove busy producer-slot handling | Add a PostgreSQL response regression holding the producer lock through expiry: refusal retains the slot until owner cleanup, then reuse succeeds. |
| C17 | Grant tests cannot detect a table omitted from the registry | Enumerate actual public tables outside installer grants and assert SELECT/INSERT/UPDATE/DELETE denial. |
| C18 | Browser release parity lacks no-skip enforcement | Duplicate of C11. |
| C19 | No-op MAC demotion emits migration evidence | Require a changed key usage before starting the transaction; verify unchanged version and absent audit on refusal. |
| C20 | Isolation CI can silently skip acceptance | Require no skips in CI and release isolation jobs. |
| C21 | OPS-02 overstates legacy trigger path pinning | Narrow evidence to the eleven named relation-reading emitters; do not claim generic table-independent guards were changed. |
| C22 | Installer has unused purge-gate mutation grants | Retain SELECT only; test actual-role INSERT/UPDATE/DELETE refusal. |
| C23 | Bootstrap audit ownership exception undocumented | Document deployment-owned bootstrap-policy-v1 history and its forward migration. |
| C24 | Unsupported progress values raise rendering errors | Admit only bounded nonnegative integers; unsupported values render without invalid numeric output. Test bool, float and oversized/unsupported values. |
| C25 | Capability test omits cap_add | Assert only Caddy receives NET_BIND_SERVICE and every other service adds none. |
| C26 | Family reauthentication revokes prior session without audit | Share transaction-bound revocation with monotonic ended-at values and one terminal event per live session; exercise actual code/token login. |
| C27 | Policy additions expand canonical history under intake lock | Traverse compact UUID lineage and filtered immutable identity projections, returning at most one conflict without historical document expansion. Existing policy/request PostgreSQL regressions pass. |
| C28 | Expired Family-session cleanup lacks matching index | Add the expires_at/id index in forward campaign migration 0032. |
| C29 | Credential acknowledgement CLI omits shared diagnostics | Configure shared logging and emit a closed startup failure category without private exception text. Tests isolate process-global logging handlers. |
| C30 | Operator dispatch and recovery wrappers lack direct tests | Exercise four implemented dispatch branches, wrong-role rejection, non-applied receipt rejection and preview deployment mismatch. |
| C31 | Failed keepalive consumes the only dirty signal | Restore dirty on transport, HTTP or malformed-response failure; keep five-minute spacing and never extend expiry on failure. All three browser engines verify retry without another keystroke. |
| C32 | Authoritative projection instants use ordinary date fields | Use UTCDateTimeField for start/end/due projections in forward migration 0032; migration drift remains clean. |
| C33 | Health connections are omitted from process budget | Rejected, false positive: auxiliary_connections already reserves two observers per web process, replica and rollout overlap. Add exact total and insufficient-headroom tests. |
| C34 | SQL password alias/overlap branches lack tests | Add duplicate, interlock and configuration-file overlap refusals. |
| C35 | Rehearsal cleanup deletes live metadata without logout evidence | Use the shared locked revocation helper before deleting session metadata and protected Django parents. Test both previously visited and unvisited invalidated sessions. |
| C36 | Unknown health becomes five false dependency failures | Preserve unknown as a typed incomplete-observation result, with fixed retry guidance and a distinct nonzero CLI outcome. |
| C37 | Generic guard functions do not pin search_path | Skipped, already handled for the supported threat model: these invoker guards read no application relations; runtime roles cannot create schemas, temporary objects or shadowing functions. C38 verifies database CREATE denial too. Do not rewrite frozen v1 migration builders for this hypothetical authority expansion. |
| C38 | Database-level CREATE escapes grant admission | Explicitly refuse has_database_privilege CREATE; exercise a real granted role, not only registry comparison. |
| C39 | Interrupted atomic-write residue prevents provisioning retry | Admit only an exact planned-name temporary suffix with private regular owner-only single-link inode checks. Preserve residue and original committed passwords; never adopt or delete unexpected material. Add interrupted-provisioning retry regression. |
| C40 | No-skip guard misses empty/collection-skipped modules | Require every requested path to contribute tests and convert collection skips to failures. Verify both cases through subprocess pytest runs. |
| C41 | Private allauth API is paired with a broad version range | Pin the already locked 65.19.2 version; no resolved dependency change. Provider upgrades must rerun the library-consumer and composed-runtime probes. |
| C42 | Missing predecessor escapes as DoesNotExist | Translate damaged lineage to the existing typed configuration refusal; add focused regression. |
| C43 | MAC backfill lacks audit evidence | Emit one transaction-bound event for nonempty batches with count and active-key fingerprint only. Exact no-work retries emit nothing. |
| C44 | Session credential epoch is mutable | Freeze credential_epoch in model and forward SQL guard; reverse migration restores the preceding guard definition. |
| C45 | Preserve free-form global Caddy error/message fields | Rejected, unsafe premise: non-access proxy/runtime failures can also contain private request URLs and upstream values. Keep both privacy filters; closed diagnostics and bounded health observations remain the supported operational channels. |
| C46 | Rejection-audit SQL failure escapes the limiter boundary | Convert database failure to LimiterUnavailable without private text, allowing existing public callers to fail closed. |
| C47 | Invalid bootstrap input leaves a committed marker | Validate exact store, OAuth and password inputs before committing the initial intent marker. Test corrected first-run identity after invalid input. |
| C48 | Exceptional end guards omit a nonterminal task state | Use the shared NONTERMINAL_STATES vocabulary for both close and schedule checks. |
| C49 | Missing parish projection bypasses one timezone comparison | Skipped, already handled: prepared projections and the mandatory audit-ownership trigger refuse the entire activation transaction without a parish row. Existing real-SQL corruption and missing-projection tests prove rollback; no campaign can commit through the alleged gap. |
| C50 | False task admission still commits work | Require the callback result to be exactly True; update established callback fixtures and verify False/None/truthy non-bools leave no task or audit rows. |
| C51 | Repeated rehearsal-gate release adds duplicate evidence | Emit only on the actual false-to-true transition; PostgreSQL exact retry emits one event. |
| C52 | Heartbeat/progress audit records grow without a workflow owner | Rejected as a scope/premise mismatch: versioned task events and value-free audit envelopes are intentional durable evidence. Operational cadence/load belongs to BG-01 and exceptional retention to its explicit later owner; those services are not enabled by this storage layer. Do not silently discard contracted evidence. |
| C53 | Optional HTTP telemetry inherits long broker waits | Use a separate pool with 50 ms per-I/O connect/read timeouts and no automatic retries; disconnect it on shutdown. This is not a claim of a total 50 ms request deadline. |
| C54 | Token-generation retry skips live derived fences | Compare caller intent separately, then recheck current epoch, source/population, configuration and key inputs. PostgreSQL epoch/population changes refuse exact retries. |
| C55 | Metrics collection nests a fresh readiness wait | Read only fresh cached dependency observations; omit unknown gauges without launching or waiting on another probe. |
| C56 | Bootstrap import leaves credential-key inventory absent | Initialize all non-metrics purpose inventories before journal retirement, under the exact bootstrap transaction/admission boundary. Give bootstrap only required SELECT/INSERT authority; composed authentication probe requires inventory-backed key locks. |

All retained findings now have a correction or explicit rejection above:
49 corrected and eight rejected/already-handled/duplicate findings. No accepted
Medium-or-higher finding remains unresolved.

Post-correction integration exposed an additional C56 consumer detail: Django's
audit INSERT requests database-default values with RETURNING, requiring audit
SELECT authority that bootstrap deliberately lacks. Inventory initialization now
appends its envelope without RETURNING, preserving append-only audit authority.
The real restricted-login bootstrap test verifies this path; no audit-history
read grant is added. Direct SQL regression also verifies C44's frozen session
epoch, and fast projection tests verify C32's naive-instant refusals.
The same composed run exposed C29's early-startup classifier importing model
code before Django settings existed. Its shared credential exception now lives
in a framework-independent module, retaining the installer's import name.
A fresh-process CLI regression verifies a fixed private refusal and structured
failure category with no Django settings or traceback.

### Final local validation

Corrected implementation: `e3fed5ca76c3dca611cc673aba2bd725e015b123`.
The accompanying evidence/CI commit adds no further application behavior.
The complete final run passes 2,704 credential-free baseline tests and all
991 PostgreSQL tests. Scoped coverage is 92.45% lines and 84.50% branches,
independently above the 80% floors. All 48 rebuilt-image/Compose checks and all
75 Chromium/Firefox/WebKit checks pass with no-skip enforcement. Ruff, formatting,
Markdown, migration drift and diff whitespace checks pass.

Rebuilt image: `sha256:ebc413d55904e7737df5ff3e88fe369251e460fa018992800cf8283c1fc2eeef`.
Local artifacts are the `parishkit-phase1c-round3-quality6`, `containers6` and
`browser6` logs, with the coverage JSON accompanying quality6; these generated
files remain outside the repository. Earlier failed integration checkpoints
are superseded, not counted as passing evidence.

All three review/fix rounds are complete, with no High/Critical issue in the
last round and no unresolved accepted Medium+ findings. Native Linux PR CI and
human merge/Gate 1 approval remain required; Phase 2 is not released by local
validation alone.

### Native Linux CI fixture correction

The first PR #21 CI run passed main validation and browser checks, but its
operational Compose test exposed a host/container path conflation. Linux pytest
staging lives below `/tmp`; the fixture incorrectly used that host path as the
container runtime root. Its writable `/tmp` mount was therefore a broad ancestor
of credentials, which the existing mount guard correctly refused. Docker Desktop's
macOS staging path did not encounter this collision.

The fixture now stages files under pytest's private host directory while using
`/opt/parishkit-integration` only inside its isolated containers. Bind sources are
mapped to the disposable volume independently of targets and rendered settings.
Focused tests cover both profiles and explicitly retain the broad-ancestor
refusal. No application code, grant, mount-admission rule or deployment behavior
changes; this is a test-portability correction, not a material application CI fix
requiring another independent implementation review. Native CI must pass before
the remaining technical checklist is closed.

Post-fix local validation passes all 43 focused mount/topology tests, both
complete Compose scenarios and 2,707 baseline tests. Application source and the
previously validated image/991-test PostgreSQL scope are unchanged.
