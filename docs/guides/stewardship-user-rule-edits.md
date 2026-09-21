# Login rule edits

This Phase 5 increment begins at verified main `e01b52ba` after PR #79's
[protected delivery](stewardship-financial-exports.md#protected-delivery). It
is the second slice of
[ADM-07](../plans/stewardship/admin-portal.md#adm-07-user-rules-and-ministry-assignments):
an Administrator changes login rules from the
[Portal users page](stewardship-portal-users.md) through previewed, versioned
configuration requests. The
[portal user management specification](../specs/stewardship/admin-portal/spec.md#portal-user-management)
controls behavior. The [review ledger](stewardship-user-rule-edit-reviews.md)
records findings, corrections and delivery evidence.

## Scope

Each hosted-domain and exact-address row offers its configured roles as
checkboxes and two actions: review a role change, or review the rule's
removal. Below each table a form adds a new rule. A change is shown back on a
review page as exactly the roles before and after, whether the rule is
created or removed, whether it is one of the specification's high-impact
expansions, how many recorded Google accounts it reaches, and whether it
removes the Administrator's own role. Confirming the signed review records one
`ConfigurationChangeRequest` against the applied digest; the installer, never
the web process, activates it, and the request page reports its progress.

Not in this increment: the autosave queue with its Applying, Applied and
Queued indicators, uncertain-outcome reconciliation and inline conflict review
(ADM-07.01); the durable security event and Administrator notification for a
high-impact expansion (the rest of ADM-07.02); Chairperson suggestions,
inherited-role review and the assignment editor (ADM-07.03 and .04). Every
role edit in this slice is a native form with one review step, which the
specification allows as ordinary low-friction administration: no fresh Google
authentication and no separate confirmation dialog beyond the review.

## Design

### One minimal patch, decided by pure code

`user_rules.rule_patch` turns the applied records, the normalized target, the
roles asked for and the request's operation identity into one patch item: an
`add`, an `update` or a `remove` on `login_rules`. A retained role keeps every
origin it had, so a Chairperson-seeded grant is never rewritten as a manual
one; a newly granted role is manual and names the request that grants it,
which the installer's manual-provenance check verifies; a removed role loses
all of its origins, as the specification requires. Removing a rule removes
nothing else: an assignment for the address stays, and the page shows it as
relying on a domain rule. A domain rule needs at least one role and can never
grant Administrator; `gmail.com` and `googlemail.com` are refused as hosted
domains by the policy schema's one consumer-domain list, which the builder
shares so the two cannot drift. Every refusal is a closed reason code, worded
by the error page, and never repeats the submitted address or domain.

The preview chooses the request key before building the patch, so the
provenance written into the patch names the request that will carry it, and
passes that key to the shared preview signer. The whole resulting policy is
validated before anything is signed, so a change that would leave the parish
without an exact-address Administrator is refused at review, not discovered by
the installer. That validation is the last-Administrator guard; the installer
repeats it at activation, and the shared confirmation rechecks the actor's
current authorization under the owning work transaction.

The installer also rechecks the actor at activation. A login-policy request,
including a Ministry assignment change, confirmed by a portal user is applied
only while that Administrator is still one under the policy being replaced,
judged by the same evaluator a sign-in uses over the base policy: a disabled
identity, or a rule revoked by another Administrator's change since
confirmation, would otherwise leave the base digest unchanged and let the
stale-base check alone activate their grant. The check runs twice. Under the
installation lock before any file is written, a refusal fails the request as
`actor_unauthorized`, a new checkpoint failure code, with nothing touched.
Inside the activation transaction the identity row is read again `FOR SHARE`,
so a disable or an address refresh waits behind the activation and cannot
commit between that read and it. PostgreSQL allows the share lock only with
an `UPDATE` privilege, so the installer role gains one on the portal user's
`id` column alone, a privilege the identity trigger's immutability and
version rules make unusable for any actual change, as the download service's
policy claim already does. The identity trigger itself takes no lock. The
activation takes the work lock, then the runtime row, then the identity row,
the order every holder of the work lock uses, and no transaction that writes
an identity row ever waits on the work lock, so no cycle can close. A change
that landed between the two checks is refused by the activation transaction
itself, which records the failure under the very lock that judged it, so no
crash can leave a refusal undecided for a later pass, the actor authorized
again by then, to judge afresh; the transition from `yaml_activated` to that
failure is one the checkpoint trigger admits for this code alone and never
from the web role, whose own checkpoint grant must not be able to strand a
selected candidate. Only then is the base YAML selected again, as an
exceptional abort does. A crash before that restore leaves the failed
request's candidate selected over an unchanged base, a state in which the
web refuses every Admin page and no new request can be recorded; the
refused request is terminal, so the installer's queue never selects it
again, and instead every install of any request finishes the restore
first, before the abort recoveries that would refuse the unrelated
manifest, and an installer pass that finds its queue empty finishes it
with no request in hand, at the cost of one small manifest read and one
pointer read while file and database agree. The crash-recovery activation runs the
same recheck and refuses the same way. Operator recovery and other system
producers are not portal users and keep their own boundaries; the web can
only ever record a request under the signed-in portal user's identity.

### The same editor discipline as the other Admin pages

The route accepts only POST with a CSRF token, refuses a query string, and
takes exactly the review fields or exactly a signed preview, through the
shared `form_action`, `sign_preview` and `confirm` helpers the parish and
Ministry editors use. Admission needs the page's own capability, and the
signed review binds the actor, the applied digest and the patch for fifteen
minutes. A review drawn from an older policy is refused as stale. The review
and error pages are never cached.

### What the review says

Before and after roles in one fixed order; an explicit deny named as such; the
high-impact expansions the specification lists, named so the Administrator
knows the change will be alerted once that follow-on lands; the count of
recorded Google accounts the rule reaches; and, when the Administrator is
removing their own Administrator role or their own exact rule while another
Administrator remains, that it takes effect on their next request. Removed
roles take effect on each person's next request after activation; an open
session is not ended.

Reach is counted as the page counts it. For an existing domain rule, the
page's own index decides: the accounts the evaluator really authorizes through
the rule, which no Chairperson confirmation can change. For a domain rule that
does not exist yet, the review counts who would be authorized once it does:
usable recorded identities that presented that claim from a matching address
and have no exact rule. For an address, the usable recorded identities at that
one address are read directly, because the page's index loads only identities
the applied policy names, and a new rule's address, such as a consumer account
that already tried to sign in, is not yet among them. The specification's
count of affected people who are signed in right now is not shown yet; it
belongs with the autosave slice, which owns the page's live state.

A stale form or review, one drawn from a policy that has since changed, is
explained on the same refusal page with a 409, whether it is caught at review
or, by the shared admission under the work transaction, at confirmation, and
it passes the same final authorization recheck as every other response on the
route.

## Fresh-install schema audit

Independent fresh predecessor and candidate databases were compared on the
disposable PostgreSQL cluster; the predecessor, verified main `e01b52ba`,
exactly matches its committed fingerprint. Two objects change and none is
added or removed: the `config_checkpoint_failure_code` check constraint admits
the new `actor_unauthorized` code; and the checkpoint transition trigger
function admits a failure with that code after `yaml_activated`, the state a
request holds when the activation transaction refuses its actor, from any
role but the web. Outside the schema, the configuration installer's grants
gain `UPDATE` on the portal user's `id` column for the share lock. The
candidate has 210 relations, 2,360 columns, 3,273 constraints, 972 indexes,
568 functions, 535 triggers and 28 policies. The strict fixture was updated
only after this inspected comparison. This is a pre-production fresh-install
baseline; no upgrade path is added and no retained database was deleted.

## Focused validation

- Four database-free cases: new rules with manual provenance bound to the
  request and an explicit deny created deliberately; retained origins kept and
  removed roles' origins dropped, with each high-impact expansion classified;
  removal as one patch item; and every refusal as a closed reason code that
  never repeats the target.
- Eight PostgreSQL cases under the real web role, installing each confirmed
  request through the real installer: a new address created with provenance
  naming the request, a domain rule widened, an address shrunk to an explicit
  deny and then removed, with the page agreeing at each step; every refusal
  explained without an echo beyond the Admin chrome's own text, a stale form
  and a review signed before another activation refused, the only
  Administrator's role protected, unknown fields, a query string, the wrong
  method, a missing CSRF token and a forged preview refused, a second
  Administrator allowed to give up their own role or rule with the review
  saying so, an Administrator grant named as the expansion it is, and a
  Ministry leader denied, with no request recorded by any of it; one signed
  review confirmed twice as one request whose provenance the installer
  accepts; a confirmed grant refused at activation as `actor_unauthorized`
  once the actor's identity is disabled, as stale once another
  Administrator's change moved the base, and, when the identity is disabled
  after the installer's first check, rolled back by the activation recheck
  with the base YAML selected again; under the real restricted installer
  role, a change applied, a crash after YAML selection recovered as a refusal
  once the actor is disabled, the transition refused for a stale base and for
  the web role, a crash between the recorded refusal and the restore leaving
  the refusal standing with the actor re-enabled while a request queued
  before it restores the base and applies, the same crash with the queue
  empty and the page refused until the installer's idle pass restores the
  base with no request in hand, a second connection's disable held off by
  the activation's share lock until it commits, and the installer's column
  grant unusable for an update that changes nothing; a review signed for one
  Administrator refused for another; a Chairperson-seeded address widened
  through the route keeping its seeded origin beside the new manual one; and
  reach counted as the page counts it, with an authorized colleague, an
  exact-address holder, an alias-domain claim, a disabled identity, a
  consumer account no rule names, and a domain rule that does not exist yet.
- The page's own eight PostgreSQL cases and the parish and Ministry editors'
  suites pass with the shared signer and confirmation changes.
- Nine browser cases across Chromium, Firefox and WebKit: every row's controls
  in every page state, Administrator never offered for a domain, the applied
  roles ticked, the review and refusal pages accessible, and without scripts a
  row's ticks and the add form posting natively to the rules route.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and three
[review/fix rounds](stewardship-user-rule-edit-reviews.md) are complete, the
first two single-source under the second September 20, 2026 Codex exemption
and the third dual-source, plus nine correction checks of the activation
guard, three dual-source and six single-source under the same exemption,
with every accepted finding fixed and the last check finding nothing to
fix. Full exact-head CI, DCO and protected
delivery remain open. M5 and Gate 3 remain open. No deployment, release,
live-provider write or database deletion is authorized by this increment.

## Protected delivery

PR #80 delivered candidate `5bf72cd4`, three logical commits plus the PR #79
receipt and the fast-selection rotation, whose tree `0b20f97c` is identical
to the retained commit-by-commit review history on
`pr/stewardship-user-rule-edits-reviewed` and to the landed tree. Exact-head
ready-candidate CI `35603873225` and DCO passed all 25 checks on the first
ready candidate, from 13:10:13 to 13:29:20 UTC on September 21, 2026 (19
minutes 7 seconds). `origin/main` had no intervening commits since the
candidate's base `e01b52ba`. Protected auto-merge landed as
`4f0465fab46eff999985a3d053ab63b363c86822` at 13:29:45 UTC and was verified on
freshly fetched `origin/main` before the next increment started. This used the
standing delivery authority, without deployment or release, and supersedes the
pending delivery checkpoint above. The cancelled runs GitHub created for the
draft head and for its delayed pull-request events are not counted as
acceptance.

The login rule edit increment is delivered: reviewed role, rule creation and
removal requests from the Portal users page, with the activation-time actor
recheck. ADM-07 stays open for its security events and notifications,
autosave queue, suggestions and assignments. M5 and Gate 3 remain open.
