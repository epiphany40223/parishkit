# Existing Member census increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-04 scope](../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps) ·
[Normative fields](../specs/stewardship/parishioner-portal/spec.md#existing-member-census)

[Review rounds and corrections](stewardship-member-census-reviews.md) retain
the exact review heads, findings and post-correction evidence.

## Boundary and starting point

Branch `pr/stewardship-member-census` starts at PR #24's verified merge,
`5c478e8a304b5de66e9f96d9096937e498d2f836`. Deliver the complete non-terminal
census fields for existing active Members as one independently testable flow:
trusted source projection, typed normalization, effective changes, atomic
submission and guarded proposals, mobile editing/review and repeat visits.

Moved-household/deceased semantics and proposed Members follow in the next
coherent increment. Ministry/financial modules and integrated Gate 2 remain
open. FAM-04.01, .04 and .05 are only partially addressed here; their task
checkboxes must remain open until the subsequent requirements pass. This
increment does not enable provider writes, delivery, deployment or release.

## Internal checkpoints

1. Define the closed existing-Member field registry, trusted source mapping,
   explicit unknown semantics, and pure validation with regression tests.
2. Expand source dependency, complete submission, effective merge and SQL
   provenance/answer guards together. Audit a fresh-install database against
   an independently verified reference of the exact starting commit.
3. Integrate clearly delineated mobile Member controls, inline validation,
   complete review, relationship context and stale-form edit preservation.
4. Run browser/database/default regression checks, complete three dual-model
   review/correction rounds, pass final-head CI and the protected merge queue.

Do not upgrade or delete retained development databases. Existing household,
session-expiry, no-draft, source-race and privacy acceptance remain required.

## Evidence

The existing-Member flow and three dual-model review/correction rounds are
complete. All accepted Medium findings are fixed; no High/Critical was found
in the final round. The linked ledger preserves exact review/correction
evidence rather than counting failed or degraded reviews as approval.

Validation passes 4,959 default tests, 263 combined response/census/audit/schema
database cases, and 147 Family browser cases across Chromium, Firefox and
WebKit. After the final recovery correction, all 110 affected Member/revisit/
audit/schema database cases pass. Ruff lint/format, tracked Markdown and model
state checks pass. Opt-in default-suite skips do not replace actual runtime
validation. These database checks test application, exact-role and fresh-
install invariants, not upgrade behavior or PostgreSQL itself.

Final-head CI and protected merge remain required. No full FAM-04 task or Gate
2 is claimed complete at this checkpoint.

## Field and source decisions

The versioned complete aggregate is `family-census-members-v1`, with
`family-inputs-v3` baselines. Relationship context is read-only and participates
in the relevant-input projection. Missing source fields retain explicit
availability; unsupported source enumeration text is offered only as the
current value and may be retained unchanged, not forged as a new choice.

Birth-date controls distinguish empty input from explicit Unknown. The browser
sends an ISO civil date or the explicit `unknown` choice, stored as date or
null respectively. A prior submitted Unknown remains visible on revisit when
source is unavailable. It cannot erase a subsequently supplied upstream date
unless there was an actual pending change. Future-date validation uses the
parish day, not the browser or host timezone.

Known-null source dates also require an explicit initial choice. Unknown
wording explains the normal Admin-reviewed removal request, rather than
suggesting a privacy-only action. Retained source text must fit the same field
bounds as the response. An oversized or unrepresentable source field yields the
controlled unavailable-form response without an unsendable form or a leaked
exception. Staff can correct that source value; the application neither
truncates it nor upgrades/deletes retained source data.
During source refresh, an unrepresentable pending field becomes a conflict
with unavailable current value and a pin to that source snapshot, without
preventing other source updates. Original submission provenance stays pinned;
obsolete intermediate comparisons are released normally after correction.
The Family form remains unavailable until that source field is usable again.
A durable WARNING diagnostic identifies only the Family DUID, Member DUID and
closed field name, never the source value. SQL independently constrains this
operational context. Endpoint diagnostics survive the failed form transaction;
refresh diagnostics commit atomically with the blocked proposal.

Phones accept US national input or an explicit international `+` country code.
The closed record retains `display` and `normalized` values, including any
extension. Comparison ignores presentation punctuation but not extensions.
The pinned [phonenumbers 9.0.39](https://pypi.org/project/phonenumbers/9.0.39/)
metadata validates possible new numbers locally; it makes no ownership,
deliverability or external lookup claim. A malformed legacy source number can
be retained unchanged, but not introduced as a new phone answer. The database
independently reconstructs source phones and guards the display/comparison
binding, closed Member set, types, civil dates, enumerations and text bounds.

The phone dependency is the only lock-file addition. An independent audit
first verified the exact merged reference against its own checked-in golden
inventory, then compared the candidate: two phone helper functions added and
four response functions and one closed operational-context function changed.
The candidate has 303 functions, fingerprint
`0f78df3b3e3c804c1b2159f5beaba609ade4a945c3dac8c52813cd6649b866a2`
after the round-2 value-free diagnostic correction.
The existing operational-event constraint additionally admits the new closed
event name; its audited constraint fingerprint is
`4dc31a17037821f8a21cf322510c84ca871630b52fc0849a1fc3630c15b7594a`.
All table, column, index, policy and trigger fingerprints are unchanged. The
reference database is preserved and has not been upgraded.
