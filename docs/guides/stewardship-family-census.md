# Family census increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-03 scope](../plans/stewardship/parishioner-portal.md#fam-03-family-census-step) ·
[Portal specification](../specs/stewardship/parishioner-portal/spec.md#family-census)

[Review rounds and corrections](stewardship-family-census-reviews.md) retain
the exact reviewed commits, findings and post-review schema evidence.

## Starting point and boundary

Branch `pr/stewardship-family-census` starts at PR #23's verified merge,
`4051b4a5250cdbfa4a8f41d23d9fab800f252b84`. The next independently testable
outcome is the complete household census step: read-only identity/registration,
home/mailing addresses, same-as-home behavior, email opt-out, review and revisit.
Include its source projection, typed validation, atomic proposal/SQL guards,
and browser/database tests in this increment rather than separate helper PRs.

The later existing/proposed/terminal Member expansion and Ministry/financial
modules remain explicitly owned by Phase 3B's subsequent increments. This
boundary reduces review size without releasing the integrated Gate 2 or
enabling real source writes, delivery, deployment or release.

## Internal checkpoints

1. Define typed household fields, availability and country-aware validation.
2. Extend the trusted source projection and effective/proposal owners, including
   exact-role SQL provenance/state checks and source-promotion reconciliation.
3. Add the mobile household step, same-as-home preservation, changed markers,
   complete review and stale-form resolution without persisted drafts.
4. Extend the real HTTP/SQL acceptance scenario and browser regressions; audit
   the fresh-install schema, complete three dual-model review/correction rounds,
   validate and land the protected PR before the next increment.

## Source and validation constraints

The current verified loader retains a primary contact address but does not
establish separate home/mailing values, country or registration date. Do not
invent home/mailing equivalence, a default source country, or a blank source
registration date. Unsupported source values retain explicit availability;
untouched unavailable fields must not manufacture proposed updates.

The source's `sendNoMail` is not the parish-wide email opt-out. Email opt-out is
manual follow-up and never changes campaign mail eligibility. The registered
address API capability is potential future handling, not permission to publish
or proof that a complete safe write payload is currently reconstructable.

Country-aware validation follows the portal specification. US state/ZIP rules
must not be imposed on international addresses; the subsystem plan's older
US-only wording has been aligned with that existing requirement.

The country selector uses the bundled ISO data in
[pycountry 26.2.16](https://pypi.org/project/pycountry/26.2.16/), with an immutable
sorted list of two-letter codes and labels. No household address is sent to a
country lookup or postal-verification service. Nonblank addresses require a
delivery line, city/locality and an explicit country; US addresses additionally
require a supported state/territory/military abbreviation and ZIP/ZIP+4 syntax.
Region/postal fields remain bounded optional text for other countries. A fully
blank address normalizes to null; it is not a fabricated upstream address.

Email opt-out preserves null only when the trusted source is unavailable or
known-null; explicit true/false remains distinguishable. The same-as-home flag
requires two explicitly submitted matching nonblank addresses. The server does
not silently copy a partially supplied value, and the browser must separately
preserve a distinct mailing draft until its confirmation flow completes.

All changes remain fresh-install-only. Never upgrade or delete a retained
development database to make a schema or test pass.

## Evidence

The pure household validators and shared text normalization pass 209 focused
tests together with the existing response-answer/comparison regressions.
The dependency lock adds only the pinned country package; existing package
versions remain unchanged. The source/SQL/UI integration is now implemented
and undergoing regression validation and review. No additional milestone or
final review gate is claimed complete yet.
PR #23's final-head CI passed every check at `c440e5b`, including
all 2,220 database tests, the 492 browser tests and both coverage floors, before
the protected merge. The [Phase 3A review ledger](stewardship-phase-3a-reviews.md)
retains the exact reviewed commits, corrections and local diagnostic evidence.

The household increment's initial acceptance checks pass 28 real PostgreSQL
tests (including exact web-role forgery rejection, explicit true/false opt-out,
complete HTTP submission and same-intent revisit). Fifteen new browser cases
pass across all three supported engines, including 320-pixel/mobile layout,
international versus US syntax, address-copy confirmation/cancellation, distinct
mailing draft restoration after Back, and competing-address choices. The
existing 45 Family browser cases and 11 database revisit cases also pass.
The default suite passes 4,655 tests with 2,804 explicit opt-in skips; those
skips do not substitute for database/browser/operational validation.

An independent fresh-install comparison against exact merged `4051b4a` first
verified the reference against its own checked-in fingerprint. The only object
deltas are the new `stewardship_response_household_source_v1` and changes to
`stewardship_response_comparison_v1`, `stewardship_response_derived_guard_v1`,
and `stewardship_submission_guard_v1`. There are 300 functions; all relation,
column, constraint, index, policy and trigger fingerprints remain unchanged.
The independently verified function fingerprint is
`c590b4510b872b89a4a2d7752bddaa3fc14841b93a7ccdaa0662a3542dd4998a`.
The exact-main reference database is preserved, not upgraded or deleted.

The combined pre-review run passes all 170 response/household/schema database
tests in 179 seconds and all 60 Family browser tests in 83 seconds. Ruff lint,
format checks and the changed guide's Markdown checks pass. These are local
checkpoint results, not a claim that the three review rounds or final CI have
completed.

## Review completion

All three dual-model rounds are complete; the linked review ledger records
each finding and correction. The final Family browser suite passes 93 tests
across Chromium, Firefox and WebKit. Python/SQL remain at the passing
182-database and 4,656-default-test checkpoint. The final schema has 301
functions, as independently audited after round 1. Final-head CI and protected
merge remain outstanding; this record does not release Gate 2.

FAM-03.01 through .04 are implemented. FAM-03.05's address/error checks pass;
successful non-census omission is integrated with FAM-05.06 when those modules
are implemented. Today unsupported module sets fail closed. The next ready
slice after merge is the existing/proposed/terminal Member census work, not
Production mail or publication.
