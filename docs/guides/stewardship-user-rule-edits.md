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
domains. Every refusal is a closed reason code, worded by the error page, and
never repeats the submitted address or domain.

The preview chooses the request key before building the patch, so the
provenance written into the patch names the request that will carry it, and
passes that key to the shared preview signer. The whole resulting policy is
validated before anything is signed, so a change that would leave the parish
without an exact-address Administrator is refused at review, not discovered by
the installer. That validation is the last-Administrator guard; the installer
repeats it at activation, and the shared confirmation rechecks the actor's
current authorization under the owning work transaction.

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
recorded Google accounts the rule reaches, counted as the page counts them:
for a domain, the accounts the evaluator really authorizes through the rule;
for an address, its usable recorded identities; and, when the Administrator is
removing their own Administrator role or their own exact rule while another
Administrator remains, that it takes effect on their next request. A stale
form or review, one drawn from a policy that has since changed, is explained
on the same refusal page with a 409, whether it is caught at review or, by the
shared admission under the work transaction, at confirmation. Removed roles take effect on each person's next
request after activation; an open session is not ended.

## Focused validation

- Four database-free cases: new rules with manual provenance bound to the
  request and an explicit deny created deliberately; retained origins kept and
  removed roles' origins dropped, with each high-impact expansion classified;
  removal as one patch item; and every refusal as a closed reason code that
  never repeats the target.
- Two PostgreSQL cases under the real web role, installing each confirmed
  request through the real installer: a new address created with provenance
  naming the request, a domain rule widened, an address shrunk to an explicit
  deny and then removed, with the page agreeing at each step; and every
  refusal explained without an echo, a stale digest refused, the only
  Administrator's role protected, unknown fields, a query string, the wrong
  method, a missing CSRF token and a forged preview refused, a second
  Administrator allowed to give up their own role with the review saying so,
  an Administrator grant named as the expansion it is, and a Ministry leader
  denied, with no request recorded by any of it.
- The page's own eight PostgreSQL cases and the parish and Ministry editors'
  suites pass with the shared signer and confirmation changes.
- Nine browser cases across Chromium, Firefox and WebKit: every row's controls
  in every page state, Administrator never offered for a domain, the applied
  roles ticked, the review and refusal pages accessible, and without scripts a
  row's ticks and the add form posting natively to the rules route.
- Ruff, formatting and Markdown lint pass.

## Checkpoint

Implementation, focused validation and one single-source
[review/fix round](stewardship-user-rule-edit-reviews.md) under the second
September 20, 2026 Codex exemption are complete, with every accepted finding
fixed. Two more rounds, full exact-head CI, DCO and protected delivery remain
open. M5 and Gate 3 remain
open. No deployment, release, live-provider write or database deletion is
authorized by this increment.
