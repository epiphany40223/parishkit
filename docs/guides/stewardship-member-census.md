# Existing Member census increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-04 scope](../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps) ·
[Normative fields](../specs/stewardship/parishioner-portal/spec.md#existing-member-census)

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

Scope recorded; implementation and validation are in progress. No additional
task or gate is complete at this checkpoint.

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
four response functions changed. The candidate has 303 functions, fingerprint
`80610e09eef36a80998358d912316942038a186b5ca7b29fcc6ef2d3e5b8cbff`.
Every table, column, constraint, index, policy and trigger fingerprint is
unchanged. The reference database is preserved and has not been upgraded.
