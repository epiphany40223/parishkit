# Parishioner portal tasks

[Task index](README.md) · [Implementation plan](../../plans/stewardship/parishioner-portal.md) ·
[Normative specification](../../specs/stewardship/parishioner-portal/spec.md) · [Milestones](milestones.md)

Each task maps to the same numbered item in its linked work package. Read that
item in full: the short label below does not replace its requirements or tests.
Follow the [execution and completion rules](README.md#execution-and-completion).

## FAM-01: Availability, code entry, and secure-link exchange

Scope and dependencies: [FAM-01 work package](../../plans/stewardship/parishioner-portal.md#fam-01-availability-code-entry-and-secure-link-exchange).

- [ ] FAM-01.01 — Build all campaign-availability and code-entry states.
- [ ] FAM-01.02 — Integrate code/token login and clean session exchange.
- [ ] FAM-01.03 — Enforce Testing acknowledgement and banners.
- [ ] FAM-01.04 — Gate every Family route by eligibility, interval, mode, and epoch.
- [ ] FAM-01.05 — Audit Family access and maintain presence metadata.
- [ ] FAM-01.06 — Test access, Testing, token, and lifecycle denial cases.

Evidence: [Phase 3A](../../guides/stewardship-phase-3a.md) integrates the existing
code/link session owner with answer-free entry, explicit Testing consent,
banner, public date availability, and fresh route admission. Its HTTP and
browser cases pass; complete lifecycle denial/acceptance and review remain open.

## FAM-02: In-memory form engine and navigation

Scope and dependencies: [FAM-02 work package](../../plans/stewardship/parishioner-portal.md#fam-02-in-memory-form-engine-and-navigation).

- [ ] FAM-02.01 — Build the accessible mobile multi-step form shell.
- [ ] FAM-02.02 — Prefill the merged Family model and change indicators.
- [ ] FAM-02.03 — Keep drafts in memory and warn on loss or expiry.
- [ ] FAM-02.04 — Integrate validation, idle warning, and activity keepalive.
- [ ] FAM-02.05 — Test navigation, lost drafts, expiry, and mobile focus.

Evidence: [Phase 3A](../../guides/stewardship-phase-3a.md) implements the first
mobile census/review flow, merged prefill, change indicators, tab-only drafts,
validation, expiry clearing, back/edit navigation and shared keepalive. Targeted
browser tests cover all three engines. Full module navigation and Gate 2
accessibility/privacy acceptance remain open.

## FAM-03: Family census step

Scope and dependencies: [FAM-03 work package](../../plans/stewardship/parishioner-portal.md#fam-03-family-census-step).

- [x] FAM-03.01 — Build Family identity, addresses, and email opt-out fields.
- [x] FAM-03.02 — Apply shared census validation and normalization.
- [x] FAM-03.03 — Implement mailing-same-as-home with preservation.
- [x] FAM-03.04 — Build changed-field and review summaries.
- [x] FAM-03.05 — Test invalid addresses and disabled census.

Evidence: [Family census increment](../../guides/stewardship-family-census.md)
implements identity/availability, typed complete household answers, guarded
proposals and mobile review/revisit. Its [three-round review ledger](../../guides/stewardship-family-census-reviews.md)
records eight accepted/resolved Medium findings, passing 182 database tests,
4,656 default tests and 93 final Family browser cases. PR #24's final-head and
merge-group CI passed; its merged tip is recorded in the increment guide.
FAM-03.05's malformed/country-aware cases pass; the
[Ministry increment](../../guides/stewardship-ministry-responses.md) now tests
census-disabled omission through the restricted HTTP/SQL and mobile browser
owners. The financial increment below completes the remaining module
combinations; Gate 2 remains open.

## FAM-04: Existing and proposed Member steps

Scope and dependencies: [FAM-04 work package](../../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps).

- [x] FAM-04.01 — Build existing-Member census sections.
- [x] FAM-04.02 — Implement moved-household and deceased semantics.
- [x] FAM-04.03 — Build proposed-Member add, edit, and removal.
- [x] FAM-04.04 — Apply terminal-aware mandatory-field rules.
- [x] FAM-04.05 — Test Member fields, dates, terminal choices, and removal.

Evidence: [Existing Member increment](../../guides/stewardship-member-census.md)
implements non-terminal census fields and has completed three dual-model
review/correction rounds, passing final-head/merge-group CI and PR #25's
verified protected merge. The [Member-request increment](../../guides/stewardship-member-requests.md)
completes terminal/proposed-Member flow, independent date work, history-scoped
revisits and SQL authority regressions. Three dual-model review/fix rounds and
local acceptance pass with [recorded evidence](../../guides/stewardship-member-requests-reviews.md).
PR #26 passed final-head CI and its protected merge is verified on `origin/main`.
Ministry choices for proposed Members belong to FAM-05; Gate 2 remains open.

## FAM-05: Ministry and financial stewardship steps

Scope and dependencies: [FAM-05 work package](../../plans/stewardship/parishioner-portal.md#fam-05-ministry-and-financial-stewardship-steps).

- [x] FAM-05.01 — Build current, join, and leave Ministry controls.
- [x] FAM-05.02 — Apply campaign Ministry selection and Admin-managed activity boundaries.
- [x] FAM-05.03 — Build financial aggregates, pledge, and installment calculation.
- [x] FAM-05.04 — Build share options and zero/one/many Member wording.
- [x] FAM-05.05 — Show financial periods and unavailable source data.
- [x] FAM-05.06 — Test Ministry, financial, and terminal-Member interactions.

Evidence: [Ministry increment](../../guides/stewardship-ministry-responses.md)
implements .01/.02 and tests the Ministry portions of .06, including proposed
Members, hidden request preservation, roster resolution and Ministry-only
campaigns. Local validation and the
[three-round review](../../guides/stewardship-ministry-responses-reviews.md)
are complete; final-head/merge-group CI passed and PR #27's protected merge is
verified on `origin/main`. At that checkpoint, financial work remained a separate
following increment and .06 stayed open.

The [financial increment](../../guides/stewardship-financial-responses.md) completes
.03–.06 with scoped source aggregates, atomic guarded submission and the mobile
editor/review/revisit flow. Three dual-model review/fix rounds are complete with
all accepted Medium+ findings corrected. All 699 browser cases pass; the linked
ledger records PostgreSQL/coverage evidence and the independently audited schema
fingerprint correction. PR #29 passed final-head and complete merge-group CI;
its protected merge `e406ecfa` is verified on `origin/main`. Integrated Family
acceptance and Gate 2 remain open.

## FAM-06: Additional information, review, and atomic submit

Scope and dependencies: [FAM-06 work package](../../plans/stewardship/parishioner-portal.md#fam-06-additional-information-review-and-atomic-submit).

- [ ] FAM-06.01 — Build the optional additional-information field.
- [ ] FAM-06.02 — Build complete response review and attestation.
- [ ] FAM-06.03 — Implement atomic Submit with Family-specific source concurrency.
- [ ] FAM-06.04 — Render Thank You, queue receipt, and invalidate session.
- [ ] FAM-06.05 — Preserve in-memory edits through errors and refreshed-baseline review.
- [ ] FAM-06.06 — Enforce Testing acknowledgement at final submission.
- [ ] FAM-06.07 — Test duplicate, concurrent-response, source-promotion, and interrupted submissions.

Evidence: [Phase 3A](../../guides/stewardship-phase-3a.md) implements additional
text, minimal complete census review, atomic Submit, separate final Testing
consent, Thank You/logout and a receipt-intent stub. HTTP/database/browser
tests cover no-change, errors, stale source/prior response and late rollback.
Remaining modules and actual Phase 4 dispatch are not claimed complete.

## FAM-07: Repeat visits and source-change merge

Scope and dependencies: [FAM-07 work package](../../plans/stewardship/parishioner-portal.md#fam-07-repeat-visits-and-source-change-merge).

- [ ] FAM-07.01 — Prefill repeat visits and prior submission time.
- [ ] FAM-07.02 — Integrate source merge without misattributing Admin edits.
- [ ] FAM-07.03 — Explain caught-up, pending, and conflicting changes.
- [ ] FAM-07.04 — Supersede derived work only upon final Submit.
- [ ] FAM-07.05 — Test repeated census, information, and Ministry changes.

Evidence: [Phase 3A](../../guides/stewardship-phase-3a.md) implements census
revisit, prior live submission time, effective-only private projection and
pending/caught-up/conflict integration with source promotion. Complete
Ministry/financial and publication-resolution acceptance remains open.

## FAM-08: Responsive, accessibility, privacy, and browser completion

Scope and dependencies: [FAM-08 work package](../../plans/stewardship/parishioner-portal.md#fam-08-responsive-accessibility-privacy-and-browser-completion).

- [ ] FAM-08.01 — Exercise the supported device and browser matrix.
- [ ] FAM-08.02 — Complete automated and manual accessibility checks.
- [ ] FAM-08.03 — Verify exclusion of form answers from persistent client data.
- [ ] FAM-08.04 — Test slow networks, expiry, delays, and server failures.
- [ ] FAM-08.05 — Measure maximum-size forms and correct UX regressions.

Evidence: Not started.
