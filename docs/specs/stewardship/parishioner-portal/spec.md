# Parishioner Family portal

The Family portal is a focused, mobile-first flow: authenticate, review/update
one Family, submit, receive confirmation, and end the session. It is not a
dashboard. Parishioner-facing language never mentions ParishSoft or exposes
internal reconciliation/review terms.

## Availability and entry

At `/`, the application evaluates configuration and campaign state before
showing a code form:

- restore review required: a neutral parish-branded maintenance message with no
  Family authentication or data access;
- unconfigured: "The system is not configured yet" with parish contact help;
- before start: the configured parish name and local start date;
- after close: the configured parish name and ended message;
- active: Family code entry; and
- no campaign: a neutral no-current-campaign message.

These pages reveal no Family information. Testing mode still honors campaign
date gates; Admin page previews remain available outside the interval.
The restore-maintenance gate takes precedence over Testing mode, dates, codes,
tokens, and existing Family sessions. Enabling it revokes Family sessions; no
Family route accepts or buffers answers until the Admin releases the gate into
Production.

The manual credential is exactly eight case-insensitive ASCII letters `A`-`Z`.
Spaces/hyphens may be stripped for friendly entry, but no digits or additional
characters are accepted. Generated codes omit `I`, `L`, and `O`, but an entered
candidate containing them is processed normally and receives the same generic
result if it does not match. Unknown, inactive, non-Parishioner, closed-campaign,
and revoked codes use the same "This Family code cannot be found or used"
result and retry link.

Family email uses `/access/<opaque-token>`, not a code query parameter. A valid
token creates the same Family session and redirects immediately to a clean
wizard URL. The generic email URL points to `/`.

An explicit Cancel and sign out action is available throughout. It warns that
in-progress answers will be lost, clears client state, revokes the server
session, and returns to `/`.

## Form state and navigation

One server response supplies a normalized baseline/effective form payload and
the enabled step definitions. In-progress edits remain in JavaScript memory for
that tab only. They are not persisted to PostgreSQL, Valkey, localStorage,
sessionStorage, cookies, logs, or analytics. Refresh, tab close, logout, or
session expiry loses edits. The expiry warning states this consequence.

Once the form is dirty, in-application navigation and supported browser
page-unload hooks warn that unsaved answers will be lost. This warning does not
create a server or browser draft: preserving the requirement that nothing is
saved before final Submit is an explicit privacy trade-off.

Active form interaction keeps the authenticated session alive without saving
answers, using the rate-limited
[activity keepalive](../architecture/spec.md#identity-and-session-security).
Passive presence polling does not extend the session. The expiry warning offers
continued interaction when the idle deadline can still be refreshed and states
when the absolute four-hour deadline cannot be extended.

Forward/back controls preserve the in-memory state, move focus to the step
heading, and never submit. A visible progress indicator names the current step
and total. Browser history cannot resubmit or expose a completed form.

Steps are assembled from enabled modules:

1. Welcome and prior-submission status.
2. Family census, when enabled.
3. One Member census/Ministry section per current/proposed active Member.
4. Add proposed Member, when census is enabled.
5. Financial stewardship, when enabled.
6. Additional information, when enabled.
7. Review and final Submit.

The welcome page says whether the Family previously submitted live answers and
shows the last submitted time in the browser timezone. Testing submissions do
not satisfy that status.

Values differing from current source data use an icon, text label such as
"Your updated value," and styling; color alone is insufficient. A source
conflict does not show the hidden current value. It says that the Family's
previously provided update remains pending and may be edited.

## Common validation

Client validation runs on blur/change and at step navigation. Invalid fields
show an accessible inline message and error style. Final Submit repeats all
validation server-side against the latest campaign/schema/authorization.

Text is Unicode-normalized, whitespace-trimmed where appropriate, length
bounded, and preserved without altering meaningful punctuation/case. Emails
use syntax validation and case-insensitive normalized comparison. Phone input
accepts international formats and stores a normalized value plus display form.
Dates use unambiguous controls, cannot be impossible/future where prohibited,
and respect Member birth/death ordering. Money accepts nonnegative USD to two
decimal places and a documented maximum suitable for reporting.

Required-but-unknown fields offer an explicit Unknown/prefer-not-to-answer
choice where defined. An untouched blank is not equivalent to explicit
Unknown. Server errors return the user to Review with a summary linked to every
problem.

## Family census

When census is enabled, show:

- envelope number, read-only;
- registration date, read-only;
- structured home address;
- structured mailing address plus "same as home"; and
- "opt out of all parish emails."

Addresses contain line 1, optional line 2, city/locality, region/state,
postal code, and country. Validation is country-aware and does not require a US
state/ZIP for international addresses.

The email-opt-out answer becomes a manual census proposal for the parish's
source systems. It does not alter mail from this campaign. Because reminders go
only to Families without any live submission, a submitted Family receives no
later reminder anyway. The non-sensitive submission receipt is transactional
campaign mail and is still sent.

## Existing Member census

Every Member is clearly delineated with name and relationship context. For a
non-terminal Member, census fields are:

- first and last name, required;
- prefix, middle name, suffix, nickname, and maiden name, optional;
- birth date, required or explicit Unknown;
- gender: Male, Female, or Unspecified;
- email, optional;
- home, mobile, and work phone, individually optional;
- marital status: blank/Unknown, Annulled, Divorced, Married, Single,
  Separated, or Widowed; and
- primary spoken language: English, Spanish, or Other with required text.

The normalized internal model distinguishes a ParishSoft blank marital value
from an accidentally omitted browser field while mapping explicit Unknown to
the supported blank on manual/API processing.

Two mutually exclusive terminal choices precede the remaining fields:

- this person is no longer a member of this Family household; or
- this person is deceased, with optional death date.

Selecting either requires confirmation, disables/skips all other census and
Ministry inputs for that Member, and creates a manual semantic request. Prior
in-step edits are ignored on final payload. A Family may mark every current
Member terminal and still submit so Staff can resolve the household.

## Proposed Members

When census is enabled, "Add a household member" creates a proposed Member with
a local UUID and the same non-terminal census fields. The Family may remove a
proposed Member before Submit. At least first/last names are required; unknown
rules match existing Members.

A proposed Member may select Ministries when Ministry stewardship is enabled.
Those requests remain linked to the local proposed Member until Staff creates
and associates an upstream Member; they are manual/workflow-only. The system
does not claim to create ParishSoft Members.

## Ministry stewardship

For each non-terminal existing/proposed Member, show current campaign-included
Ministries in deterministic case-insensitive name order with DUID as final tie
breaker.

Current memberships appear first, each with an unchecked "wishes to stop"
control. Existing memberships are excluded from join choices. "Join another
Ministry" expands/searches the potentially long selected-Ministry list only on
demand and supports multiple choices. Selecting and then deselecting returns to
no requested change.

The UI does not promise that a request changes a roster automatically. It
states that a Ministry leader or parish staff member may follow up. A repeat
submission uses the latest effective requested state; removing an unresolved
choice cancels/supersedes its workflow while retaining history.

## Financial stewardship

When enabled, the page shows read-only aggregates from the latest promoted
snapshot and configured funds:

- prior/current-period Family pledge; and
- current-period contributions through the displayed data-as-of timestamp.

Unavailable or incomplete upstream data displays "Unavailable" with an as-of
warning, never `$0.00`. Individual contribution transactions are not shown.

The Family must enter an annual upcoming-period pledge. `$0.00` is valid. For a
positive pledge, select exactly one frequency: weekly, monthly, quarterly, or
annual. The UI divides by 52, 12, 4, or 1 using decimal arithmetic and displays
an approximate two-decimal installment; annual total remains authoritative and
the page notes the final payment may differ slightly.

The configured upcoming start date is prominent, with text that the pledge does
not take effect before it. If campaign and period overlap, the Admin-confirmed
configuration is displayed accurately rather than asserting the start is
future.

Share methods are a multi-select of campaign-versioned options. Default content
is based on:

- bank-sent check;
- existing parish online giving, with permission to update the amount;
- begin using parish electronic giving;
- stock gift;
- IRA distribution;
- offertory envelopes; and
- Other with required text.

Labels substitute parish name/year and use "I" only when the effective Family
contains exactly one active Member; otherwise they use "We." Proposed Members
count, while terminal Members do not. With a zero pledge, frequency/share
methods are optional but allowed to express a non-cash intent.

The page does not collect bank/card credentials or initiate a payment.

## Additional information

When enabled, display the campaign-authored prompt and a bounded multiline text
field. Repeat visits prefill the latest effective text. A new Staff follow-up
item is created only when a nonblank value differs from the prior effective
text. Re-submitting unchanged text does not duplicate work; clearing it does not
erase an older follow-up record.

## Review and submission

Review presents every enabled section, clearly marks changed values and
requests, and provides Edit links back to steps. It shows annual pledge and
approximate frequency amount, but never hidden upstream conflicts/internal
states. The final button is unambiguously labeled Submit to `<Parish name>` and
is protected against double clicks.

The request includes effective submission/source version IDs. A stale version
is rejected as specified by [submission concurrency](../data/spec.md#submission-concurrency).
If eligibility/campaign closes before submit, no answers save and an
appropriate status page appears.

On success, the response renders the campaign-versioned Thank You content,
clears all client form state, and revokes the Family session. When a deliverable
eligible Family-head address exists, it queues the receipt defined by
[background processing](../background-processing/spec.md#submission-confirmation).
Having no deliverable recipient is a recorded non-error and never prevents the
submission. The receipt gives parish, campaign, Family display name, UTC-derived
browser/local submission time, and contact/help information but no census,
Ministry, additional-text, pledge, code, or secure-token values.

## Repeat visits and source changes

A valid Family may return during the campaign using the same code/token. The
form is built from the current source snapshot merged with the latest effective
live response according to the [effective-value rules](../data/spec.md#effective-value-merge).
All prior Family proposals still differing from source are visibly marked.

The new final submission is a complete replacement effective answer, not a
partial patch, while the old immutable version remains in history. Current
participation continues to count the Family once on the date of its first live
submission.

## Testing mode

During Testing mode every otherwise eligible Family code/token can use the
portal during campaign dates. Immediately after successful authentication and
before any household data is displayed, an interstitial states that this is a
test, answers will be permanently deleted before launch, the response will not
count, and the Family will need to respond again in Production. The user must
explicitly choose **Continue with test** or sign out.

Every form step has a persistent, non-color-only Testing banner repeating that
answers are disposable. The final review requires a separate unchecked
acknowledgment immediately beside a **Submit test response** button. The
acknowledgment says that this is not the Family's campaign response and will be
deleted. Server validation requires it; prior acceptance of the entry
interstitial is not sufficient.

Submissions are prominently marked Test on the Thank You page and administration
views. The Thank You content explicitly says the campaign response has not been
recorded, the test will be deleted, and the Family must return during Production
or contact the parish if it expected to submit a real response. Test submissions
do not:

- count as participation or pledge;
- suppress live invitation/reminder eligibility;
- prefill a later live visit;
- create live census/Ministry/additional-information work; or
- send a receipt to the intended Family.

Testing-message routing follows the single normative
[mode-routing policy](../background-processing/spec.md#mode-routing). Production
transition deletes the test submissions and sensitive associated workflow/audit
detail as defined by the Admin specification.
