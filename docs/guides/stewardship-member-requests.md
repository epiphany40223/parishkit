# Member household requests increment

[Coordinating tasks](../tasks/stewardship/overall.md#phase-3b-complete-family-flow) ·
[FAM-04 plan](../plans/stewardship/parishioner-portal.md#fam-04-existing-and-proposed-member-steps) ·
[Portal specification](../specs/stewardship/parishioner-portal/spec.md#existing-member-census) ·
[Proposal semantics](../specs/stewardship/data/spec.md#proposed-changes)

## Boundary

Branch `pr/stewardship-member-requests` starts from PR #25's verified merge,
`eeb346342fe7c78bd4295801f6800dc5f6ec44fb`. Complete the remaining FAM-04
terminal and proposed-Member flow, including guarded complete submissions,
manual semantic proposals, repeat visits and mobile editing/review. The linked
specifications control the fields, confirmation and independent death-date
handling; this guide does not redefine them.

Ministry selections for proposed Members follow with FAM-05. No provider write,
automatic upstream Member creation, deployment or release is included. Gate 2
remains open for the remaining enabled-module flows and integrated acceptance.

## Internal checkpoints

1. Add pure terminal/proposed-Member validation and regression cases, retaining
   the closed existing household identity set and local UUID identities.
2. Extend the complete aggregate, effective presentation, proposal lifecycle,
   source dependencies and independent SQL reconstruction together. Audit a
   fresh database against the exact starting baseline without upgrading or
   deleting retained development databases.
3. Integrate confirmation, terminal-aware validation, proposed-Member add/edit/
   removal, complete review and stale-form edit preservation in tab memory.
4. Pass unit, exact-role database and real-browser tests, complete at least
   three dual-model review/correction rounds, then pass final-head CI and the
   normal protected merge queue before continuing.

## Evidence

The complete ordinary/terminal/proposed aggregate and mobile controls are
implemented, and three dual-model review/fix rounds satisfy the local exit rule.
Final local validation passes 4,998 default tests and 121 authority/request/
revisit/rehearsal-cleanup/schema database cases after the final SQL restriction.
The broader 197-case response database suite passed before that restriction;
621 full browser cases pass across Chromium, Firefox and WebKit on the unchanged
browser code. Ruff, model-state drift, Markdown and independent fresh-schema
checks pass. The [review ledger](stewardship-member-requests-reviews.md) records
exact reviewed endpoints, findings, corrections and verification. FAM-04's
implementation acceptance is complete; final-head CI and protected PR merge
remain required before the next increment starts. This is not Gate 2 completion.
PR #25's closure is recorded in the [ordinary Member guide](stewardship-member-census.md).

## Implementation contracts

The fresh-install form version is `family-census-household-members-v1`, with
`family-inputs-v4` dependencies. Existing active Members still require the
exact source-owned DUID set. Terminal answers are a closed confirmed variant;
ordinary in-step fields are discarded before persistence, and death/birth
ordering uses the trusted recorded birth date, not an ignored in-step edit.
An omitted death date cannot become a proposal to clear a recorded date.

Proposed Members are keyed by canonical nonzero local UUIDs in a separate
complete collection, bounded to 100 entries as a resource guard. Each manual
`new_member` proposal contains the whole normalized ordinary Member structure.
Typed comparisons preserve same-intent review decisions across phone display
changes. Revisit uses Family-submitted values, not unpublished Admin edits;
withdrawal cancels actionable work while retaining immutable history. No
provider association or automatic creation is inferred from a UUID.

Terminal semantics and local additions remain manual staff work during source
refresh. A date catching up upstream can resolve its independent field proposal
but not deceased status; disappearance or ineligibility is not treated as proof
that staff completed the requested semantic action. The later manual-resolution
workflow owns that outcome. Same-Family death-date comparison remains available
after deceased/inactive status changes. Missing or transferred Members retain
blocked date work without reading another household's data. A surviving
household's later response does not silently cancel these independent requests;
completed semantic work is not preselected or reopened by an untouched revisit.
Preserved terminal/date records remain discoverable across intervening responses
within the same Family/campaign/mode/rehearsal namespace, so returning Members
see pending intent and later requests supersede or withdraw it without duplicate
actionable work. Repeated unchanged scope blocks do not churn versions or pins.

The browser keeps ordinary edits, terminal controls and proposed-Member edits
only in tab memory, serializes the complete variant at final Submit, and clears
it on success/expiry. Competing terminal or whole proposed-Member requests
require an explicit choice on refreshed-baseline review, including a concurrent
withdrawal. No new persistent draft, provider access or client storage is added.

The independent schema audit first verified the exact starting merge against
its own golden inventory in a new preserved reference database, then compared
fresh candidate objects. Five functions change: typed comparison, scoped source
reconstruction, aggregate guard, derived-proposal guard and the closed diagnostic
context for death-date source failures. There are still 303 functions, with
fingerprint `e362f7e055fa37867f7067b9036a4b45500f18736564b42644f8deda13a69ae4`.
All non-function fingerprints are unchanged. No retained database was upgraded
or deleted; all database checks concern current application behavior.
