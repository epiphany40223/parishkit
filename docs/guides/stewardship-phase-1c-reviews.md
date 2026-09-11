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
