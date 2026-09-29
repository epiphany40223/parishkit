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
- closed for maintenance by an Administrator: a friendly "Sorry, the site is
  temporarily unavailable" page with the Administrator's optional message and a
  "Try again" link; no Family sign-in or form data access, and no Family answer
  is saved, although an open session can still be kept alive or ended (see
  [Family portal maintenance](../admin-portal/spec.md#family-portal-maintenance));
- unconfigured: "The system is not configured yet" with parish contact help;
- before start: the configured parish name and local start date;
- after close: the configured parish name and ended message;
- active: Family code entry; and
- no campaign: a neutral no-current-campaign message.

These pages reveal no Family information. In Production, lifecycle state and
the resolved boundaries govern access. In Testing, the one current `draft`
campaign is treated as active solely for portal gating while the current instant
falls inside its resolved interval; before/after pages still apply outside that
interval, and the Testing banner below remains mandatory.
Admin page previews remain available outside the interval.
The restore-maintenance gate takes precedence over Testing mode, dates, codes,
tokens, and existing Family sessions. Enabling it revokes Family sessions; no
Family route accepts or buffers answers until the Admin completes state-aware
restore release. Live Production Family access resumes only when that release
produces an `active` campaign in Production. A released `draft` in Testing may
provide the Testing rehearsal access described below and follows the Testing
date-gating rule above; every other resulting state shows its ordinary no-
campaign, before-start, or ended page.

Manual credential generation, canonicalization, and generic denial follow the
[Family credential security policy](../architecture/spec.md#family-credential-security).
Friendly entry always removes ASCII spaces and hyphens before uppercasing and
validating exactly eight ASCII letters; digits and other characters remain
invalid. An entered candidate containing `I`, `L`, or `O` still follows the
ordinary mode-scoped lookup/denial path; a reserved-leading-`I` rehearsal code
can succeed only in its current Testing epoch. Production and Testing
credentials are not interchangeable. Every Family request checks the session's
mode and rehearsal epoch where applicable, including keepalive and Submit.
Unknown, inactive, non-Parishioner, closed-campaign,
and revoked codes use the same "This Family code cannot be found or used"
result and retry link.

Family email uses `/access/<opaque-token>`, not a code query parameter. A valid
token creates the same Family session and redirects immediately to a clean
wizard URL. The generic email URL points to `/`.

An unknown, revoked, rotated, closed-campaign, or ineligible-Family token creates
no session and shows the same generic "This secure Family link cannot be found
or used" page with a link to manual code entry. Failed attempts follow the
[bounded authentication audit policy](../architecture/spec.md#identity-and-session-security);
successful logins remain individually audited. Because the token has 256 bits of
entropy, failures do not consume guessable-code counters; ordinary request-
abuse limits still apply uniformly through the
[secure-link anti-flood policy](../architecture/spec.md#identity-and-session-security).

An explicit Cancel and sign out action is available throughout. It warns that
in-progress answers will be lost, clears client state, revokes the server
session, and returns to `/`.

## Form state and navigation

One server response supplies a normalized baseline/effective form payload,
the enabled step definitions, and the server-issued baseline reference defined
by [submission concurrency](../data/spec.md#submission-concurrency).
Only version/pinning metadata is retained for that baseline, never unsaved
answers. In-progress edits remain in JavaScript memory for
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
when the absolute four-hour deadline cannot be extended. When the session ends,
a single red notice says so (and that unsubmitted changes were not saved, or,
after an uncertain submission, to sign in again to check it) with one "Sign in
again" link; in Testing it sits below the Testing banner, which is always the
top-most bar, above the page title.

Each step is its own page. Back/Next controls preserve the in-memory state,
move focus to the step heading, and never submit; Next first checks only the
current page and keeps the Family there with inline errors, focus on the first
unanswered question, and a note beside the navigation buttons naming it
("Please check “How would you like to share?”."), which also describes the
focused field for assistive technology. Only a choice group (share methods) is
outlined in red, never a whole section. A "Step N of M"
line names the current step, and a segmented step bar like the setup wizard's
has one button per step: each is named by its step, shows a "Step N of M"
tooltip on hover or keyboard focus, and jumps to that page on click or tap.
Review (the last segment, or Review response on the last page) first requires a
Family that has not submitted before to have viewed every page: otherwise it
opens the first page not yet viewed and says so. It then checks every page and,
if an answer is missing, opens that page with the same note naming the
question, so the response cannot be completed until all steps are done. A
returning Family, who has submitted before, may go straight to Review. Browser Back/Forward move between pages. Every page shows the
Family's name (for example "The Squyres Family") so the Family can confirm the
right household is open; ParishSoft's mailing name and the session deadline are
not shown. Browser history cannot resubmit or expose a completed form.

Pages are action-first, because many Families answer on a phone. The Back/Next
bar (Back to edit/Submit on Review) is sticky at the bottom of the screen, so
the next action is always one tap away. Parish-written text is always shown in
full, exactly as the Administrator wrote it, with no "Read more" collapse.
Occasional field help (phone formats, death-date review, the email opt-out)
sits behind a small "i" toggletip beside the label, which opens on click, tap,
Enter or Space, never on hover, and closes with Escape or a click elsewhere.
Help that every Family needs stays visible, including the birth-date Unknown
explanation below and the pledge's "intention only" statement.

Optional campaign images appear when the Admin has set them for the current
campaign: a small icon (about 96 CSS pixels) above the heading of the Member,
financial and closing pages, and on the Welcome page below the returning-Family
"last submitted" notice (or, without one, first), above the parish's intro text
or, when there is none, above the "Welcome" heading.
The wide campaign banner is used in emails only, not on the Family pages. They are
decorative (empty text alternatives), since the headings and text already carry
their meaning, and scale down to fit a phone screen.

Steps are assembled from enabled modules:

1. Welcome and prior-submission status.
2. Family census, when enabled.
3. One Member page per current active Member when census or Ministry
   stewardship is enabled; its census and Ministry subsections appear only when
   their respective modules are enabled.
4. Add and fully edit proposed Members, when census is enabled, including their
   enabled census and Ministry subsections.
5. Financial stewardship, when enabled.
6. Closing, a content-only page shown only while the campaign's closing content
   has visible text; removing or emptying that content removes the step.
7. Additional information, when enabled.
8. Review and final Submit.

When the Family has submitted before, the welcome page opens with "You last
submitted your renewal on DATE AND TIME. You can review, change and submit
again as many times as you like; your most recent submission is the one we
use." The time is shown in the campaign's time zone. In Testing mode it reports
the Testing submission from the current rehearsal; live submissions and other
rehearsals never count. Families that have never submitted see no banner.

Below the welcome text the Family may check "Because of physical limitations,
I/we cannot attend Mass or prayer services at this time." The answer is
recorded with every response (unchecked by default, prefilled on a repeat
visit), shown on Review when checked, and reported to Staff; it is never
written to ParishSoft.

The sign-in page shows only its title, the code field and the parish's short
sign-in instructions; parish contact details appear on the access-denied page
instead. The title is "*campaign name* login", using the name the
Administrator set in Campaign settings. A campaign without a name falls back
to its modules: "Family stewardship login" (Ministry or financial
stewardship), "Family census login", or "Family stewardship and census login".

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

Every Member is clearly delineated by name. ParishSoft's household
relationship ("Head", "Spouse") is an internal parish designation and is not
shown to Families. For a non-terminal Member, census fields are:

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

A missing or known-null source birth date does not preselect Unknown; only a
prior explicit Family answer may prefill that choice. Choosing Unknown instead
of a recorded date requests a blank date through the normal Admin-reviewed
change workflow. The control and review summary must explain that it requests
removal of any recorded birth date, not describe it as merely withholding an
answer from this campaign. It never clears the source record automatically.

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
Ministries eligible under the
[Admin-managed activity policy](../admin-portal/spec.md#ministry-activity-management),
in deterministic case-insensitive name order with DUID as final tie breaker.
Inactive Ministries are hidden from current memberships as well as join/leave
choices. Hiding an existing membership or earlier request does not delete it or
turn omission into a new leave/withdrawal action. Recheck the same policy at
submission, including the linked stale-form reconfirmation requirement.

Current memberships appear first under "Current ministries", each stating
its choice once as "Continuing" (the default) or "Stop participating". Leaving
is always honored; the form does not describe it as a request that may be
declined. A Ministry set to "Stop participating" is highlighted in the
attention (amber) colour. Existing memberships are excluded from join choices.
"Click here to join more ministries" ("Tap here…" on a touch-only device)
expands/searches the potentially long selected-Ministry list only on demand and
supports multiple choices, and the chosen ministries stay listed under
"Joining:", one per line, while it is collapsed. Selecting and then
deselecting returns to no requested change. Review lists each Member's
ministries as "Will continue", "Stopping" and "Joining", the last two as
bulleted lists.

A repeat submission uses the latest effective requested state; removing an
unresolved choice cancels/supersedes its workflow while retaining history.

### Talents and "cannot participate"

At the bottom of each Member's page, below the ministry updates, a "Talents to
share" panel (styled like Ministry participation) asks: "If you have a special
talent that you would like to share with your parish family, please select it
below." It offers the campaign's talent checkboxes, which an Admin edits
alongside the share options (see
[Admin portal](../admin-portal/spec.md#member-talents)). A campaign that never
edited them offers the built-in defaults: Painter, Florist, Seamstress,
Carpenter, Attorney, Gardener and Other. An option marked for free text (Other)
requires a short description, at most 200 characters. No talent is required.

Above the Ministry choices, each Member may check "Because of physical
limitations, I/we cannot participate in any ministries at this time." While
checked, every current Ministry is set to "Stop participating", every join
choice is cleared, the Ministry choices and the join-more-ministries disclosure
are disabled for pointer, keyboard and assistive technology alike, and the
talents question is hidden and no talents are sent. Unchecking restores the
Family's own earlier choices, talents included. The server rejects a response in which such a
Member continues or joins any Ministry. Talents and this answer are recorded
with the response and are never written to ParishSoft.

## Financial stewardship

When enabled, the page shows one read-only sentence of giving history from the
latest promoted snapshot and configured funds: "As of *date*, you have
contributed *amount* towards your *comparison year* pledge", or "… *amount* in
*comparison year*" when there was no prior pledge. The prior pledge
amount and the records' refresh time are not repeated. Money on Family pages
omits zero cents ("$1,200").

Unavailable or incomplete upstream data says so instead, never `$0.00`.
Individual contribution transactions are not shown.

The Family must enter an annual upcoming-period pledge. `$0.00` is valid. For a
positive pledge, select exactly one frequency: weekly, monthly, quarterly, or
annual, and at least one share method whenever any are offered; both are
required, in the browser and by server validation. The UI divides by 52, 12, 4,
or 1 using decimal arithmetic and shows each payment's amount. When the pledge
divides evenly ($6,000 monthly) it is stated plainly ("$500 per month");
otherwise it is "Approximately" a two-decimal amount, the annual total remains
authoritative, and the page notes the final payment may differ slightly.

Review shows the financial answer as "Your *year* pledge: *amount*", followed in
parentheses by the payment amount under the same exact-or-approximate rule, and,
for a positive pledge, "This pledge starts on **start date**" ("began on" once
the period has started) with the date in bold. It does not
repeat the giving history, and neither Review nor its default text adds a
"nothing is sent until Submit" prompt.

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

Labels substitute parish name/year and use "This household" when the effective
Family contains zero active Members, "I" for exactly one, and "We" for two or
more. Proposed Members count, while terminal Members do not. The financial step
remains available with the same validation when no Members remain; marking all
Members terminal does not discard or clear the Family's pledge/share answers.
With a zero (or not yet entered) pledge, the frequency and share-method fields
are hidden, cleared and not required, and the form submits neither.

The Family may instead check "Because of financial limitations, I/we cannot
contribute financially at this time." While checked, the pledge, frequency and
share-method fields are hidden and not required, and the response records a
zero pledge marked as "cannot contribute"; unchecking restores the Family's
earlier entries. The server rejects a pledge, frequency or share method
submitted with this answer.

The `campaign_year` placeholder consistently means the configured campaign year
label in Admin previews, page content, emails, share labels and Ministry
packets. When no label is supplied it is the start year of the campaign's
upcoming financial period, or the campaign start year when the campaign has no
financial period. Use `financial_period`, `financial_start` and
`financial_end` for the exact pledge dates. Preserved unresolved census
intent still contributes to the effective household count if census is later
disabled; expose the count without exposing disabled census request details.

The page does not collect bank/card credentials or initiate a payment.

## Additional information

When enabled, display the campaign-authored prompt and a bounded multiline text
field. Repeat visits prefill the latest effective text. A new Staff follow-up
item is created only when a nonblank value differs from the prior effective
text. Re-submitting unchanged text does not duplicate work; clearing it does not
erase an older follow-up record but transactionally marks the current item
withdrawn. Replacing text supersedes the prior item so Staff do not act on
obsolete content while history remains auditable.

## Review and submission

Review presents every enabled section, clearly marks changed values and
requests, and provides Edit links back to steps. It shows annual pledge and
approximate frequency amount, but never hidden upstream conflicts/internal
states. The final button is unambiguously labeled Submit to `<Parish name>` and
is protected against double clicks.

The request includes effective submission/source version IDs and its bound form-
baseline reference. An unrelated source promotion does not invalidate the form;
the server compares relevant form inputs under
[submission concurrency](../data/spec.md#submission-concurrency). A changed
effective Family response or relevant source input requires review using a
refreshed baseline while unsaved edits remain only in tab memory. Clearly
distinguish updated parish records from proposed answers, preserve unaffected
edits, and require resolution of invalid/competing choices and a new Submit;
never silently overwrite refreshed records with unchanged old form values.
If eligibility/campaign closes before submit, no answers save and an
appropriate status page appears.

On success, the response renders the campaign-versioned Thank You content,
clears all client form state, and revokes the Family session. When a deliverable
eligible Family-head address exists, it queues the receipt defined by
[background processing](../background-processing/spec.md#submission-confirmation).
Having no deliverable recipient is a recorded non-error and never prevents the
submission. The receipt gives parish, campaign, Family display name, UTC-derived
submission time rendered in the campaign timezone snapshot with its timezone
abbreviation as one compact block, but no census, Ministry, additional-text,
pledge, code, or secure-token values. Contact and help information comes from
the parish-authored confirmation email and its `submission_confirmation` block
(see [data](../data/spec.md)); the default block gives the parish office phone
and email. Browser confirmation/history pages
still render timestamps in the browser timezone; email uses campaign time
because no browser context exists when the worker renders it.

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

During Testing mode every otherwise eligible Family may use the portal during
campaign dates through its current rehearsal-epoch code or token under the
[credential policy](../architecture/spec.md#family-credential-security).
Production credentials are not accepted in Testing. Testing has no entry page
or interstitial: after successful authentication the form opens straight away,
and the Testing banner (below) is the only mode notice. If the form cannot be
loaded, the page says so and offers **Try again**.

Every form step has a persistent, non-color-only Testing banner repeating that
answers are disposable, and the final button reads **Submit test response**.
The banner is the mode notice: neither entry nor final Submit asks for a
separate acknowledgment checkbox (#243). The server takes the mode from the
admitted session and its baseline, never from the browser, so a Testing
baseline cannot back a live submission.

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
