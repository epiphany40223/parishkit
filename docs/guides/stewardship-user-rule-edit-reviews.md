# Login rule edit review ledger

Review evidence for the [login rule edit increment](stewardship-user-rule-edits.md).
Severities are the raw reviewer values. Findings below the tool's
Medium/confidence cutoff are counted but listed only where they were acted on or
deliberately left.

At delivery the review corrections are squashed into logical commits, and the
complete commit-by-commit history, including every reviewed SHA below, is pushed
to `pr/stewardship-user-rule-edits-reviewed` with a tree identical to the
delivered head. That branch is review evidence only and is never merged. Until
it exists the reviewed SHAs are on the PR branch itself.

## Round 1, single-source under the second exemption

Reviewed `05fac078`, the complete diff from main `e01b52ba`. The Claude
reviewer completed with 11 findings, one Medium and ten Low; Codex exited
without output. Under the exemption this counts as round 1. Accepted and
fixed:

- **Medium: the review counted reach differently from the page.** For a
  domain it counted every identity that presented the claim, including ones
  with an exact rule, alias-domain identities and disabled ones, while the
  page's column counts only the accounts the evaluator authorizes through the
  rule. The review now indexes the applied policy the way the page does and
  reports the same number for a domain, and usable recorded identities for an
  address, worded as what it counts.
- Low: the shared confirmation rechecks the capability the page admitted
  with, passed by the caller, so the two cannot diverge; refusals render with
  the request, so they keep the Admin chrome and session deadlines; a stale
  form or review gets the page's own explanation with a 409 rather than the
  editors' JSON conflict, and a case now signs a review, activates another
  change, confirms and is refused with nothing requested; removing one's own
  exact rule is explained as the loss of Administrator it is; the unused
  `deny` context and a second role-order source are gone, the role order now
  derived from the labels; the query string is refused through the shared
  helper; the refusal helper in the unit test asserts the exception carries
  only its code; the last-Administrator refusals are matched by a phrase the
  domain-roles message does not share; and the guide states the collected
  count of the page's own cases.

Post-fix validation: four database-free, two PostgreSQL and nine browser cases
passed locally, with the page's own suite and the parish and Ministry editors.

## Round 2, single-source under the second exemption

Reviewed `f58d9a58`, the complete diff from main `e01b52ba`. The Claude
reviewer completed with ten findings, two Medium and eight Low; Codex exited
without output. Under the exemption this counts as round 2. Accepted and
fixed:

- **Medium: an address's reach was read from the page's bounded index.** That
  index loads only identities the applied policy names, so a new rule for an
  address outside every configured domain, the common consumer-account case,
  was told no identity was recorded when one was. The address count is now
  read directly for that one address; the domain count keeps the page's index,
  which no Chairperson confirmation can change.
- **Medium: the round-1 reach correction had no test with real identities.** A
  case now records an authorized colleague, an exact-address holder, an
  alias-domain claim, a disabled identity and a consumer account with one
  usable and one disabled identity, and asserts the domain review equals the
  page's column, a new domain rule reaches nothing yet, and the consumer
  address is counted although no rule names it.
- Low: the own-rule removal wording is asserted; confirming one signed review
  twice is one request whose provenance the installer accepts; the label
  helper is public under its own name; the stale refusal passes the route's
  final authorization recheck like every other response; the consumer-domain
  list lives in the policy schema and is shared with the builder, so the
  schema now refuses `googlemail.com` too; the policy refusal names only the
  cause this route can produce; and the guide is re-flowed.

Post-fix validation: four database-free, four PostgreSQL and nine browser
cases passed locally, with the page's own suite and the parish and Ministry
editors.

## Round 3, dual-source

Reviewed `63abf8e4`, the complete diff from main `e01b52ba`. Both sources
completed: Codex answered with one validated High finding; Claude returned one
Medium and ten Low. Both validated findings were accepted and fixed.

- **Codex, High: activation did not reauthorize the actor.** A confirmed
  login-policy request entered the ordinary pipeline, which checks the actor
  at intake and the base digest at activation; disabling the actor's identity
  or revoking their rule since confirmation leaves the digest unchanged, so
  the installer would still activate their queued Administrator or domain
  grant. The installer now rechecks, under the installation lock, that a
  confirming portal user is still an Administrator under the policy being
  replaced, and fails the request as `actor_unauthorized` otherwise, a new
  checkpoint failure code that is the one schema change of this increment;
  system producers, which are not portal users, keep their own boundaries. A
  case disables the actor after confirmation and proves nothing is applied,
  and another has a second Administrator remove the actor's role, which moves
  the base and is refused as stale.
- **Claude, Medium: the guide's validation count lagged the tests.** It said
  two PostgreSQL cases where four existed; it now describes every case.

Acted on from the ten Low findings: the task map says how far review has
progressed; the guide says the specification's count of affected people who
are signed in now belongs with the autosave slice; a domain rule that does not
exist yet reaches the usable identities that presented its claim from a
matching address and have no exact rule, with a case; `googlemail.com` is
refused by the builder and by the schema in the unit test; a Chairperson-seeded
address widened through the route keeps its seeded origin beside the new
manual one, installed; a review signed for one Administrator is refused for
another; the review no longer promises that before and after roles are
recorded durably, which the deferred security event will do; the route's final
recheck compares the identity as the page's does; `StaleRecordError` is in the
outer handler; and the review pins the configuration it was drawn against for
the chrome. Left as is: the last-Administrator guard counts configured
exact-address Administrator rules, as the schema always has, so a remaining
Administrator with every identity disabled still counts; a portal that can
lock itself out needs the operator recovery path, not a weaker guard.

Post-fix validation: four database-free, seven PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors and the installer's own suites.

## Round 4, correction check, dual-source

Reviewed `a4a14260`, the complete diff from main `e01b52ba`, as a check of
the round-3 activation guard. Both sources completed: Codex answered with one
validated High finding; Claude returned one Medium and five Low. Both
validated findings were accepted and fixed.

- **Codex, High: the recheck was not serialized with identity changes.** The
  installation lock serializes configuration changes, not portal-user rows,
  so an identity disabled or refreshed after the installer's unlocked read
  and before activation would still have had its grant applied. The recheck
  now runs twice: before any file is written, and again inside the activation
  transaction with the identity row read `FOR SHARE`, so no such change can
  commit between that read and the activation; a change that lands between
  the two checks rolls the activation back, the base YAML is selected again as
  an exceptional abort does, and the request fails as `actor_unauthorized`,
  which the checkpoint transition trigger now admits after `yaml_activated`,
  the second schema change of this increment. The same locked recheck guards
  the crash-recovery activation, so a crash between a refused activation and
  the YAML restore cannot let recovery activate the refused request. A case
  disables the actor once the candidate YAML is selected and proves the
  rollback, the restored selection and that no activation exists.
- **Claude, Medium: the guide's case count lagged again.** Seven PostgreSQL
  cases, stated as such in the guide and the round-3 ledger line.

Acted on from the five Low findings: the actor is judged by the sign-in
evaluator over the base policy the installer already holds, so no second
configuration load and no `ConfigError` that could be misread as a malformed
candidate; the installer's module docstring and the configuration activation
guide state the one exception to "actor UUIDs are attribution"; and the guide
says the gate covers Ministry assignment changes too, since they are login
rules.

Post-fix validation: four database-free, seven PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors and the installer's own suites.

## Round 5, correction check, dual-source

Reviewed `d7b128fc`, the complete diff from main `e01b52ba`, as a check of
the round-4 serialization. Both sources completed: Codex answered with one
validated High and one Medium; Claude returned one High, two Medium and six
Low. All five validated findings were accepted and fixed.

- **High, both sources: the locked read needed a privilege the installer does
  not hold.** PostgreSQL requires an `UPDATE` privilege to read `FOR SHARE`,
  and the configuration installer and admin recovery roles hold `SELECT` on
  portal users only, so the first confirmed rule change in production would
  have failed inside the activation transaction after YAML selection and
  wedged installation. The recheck is now a plain read under the shared work
  lock the activation transaction already holds, and the portal-user update
  trigger takes that lock too, so an identity change is serialized with an
  activation judging that identity without any new grant. A case installs
  through the real restricted installer role.
- **Medium, both sources: a crash between the YAML restore and the record
  wedged the request.** The request stayed at `yaml_activated` with file and
  database agreeing, and a retry re-entered the apply path, whose `prepared`
  checkpoint the trigger refuses from that state. The installer now records
  such a request as the refusal it was from that state alone, never
  re-entering the apply path even once the actor is authorized again, with a
  case for the crash and the retry.
- **Medium, Claude: the recovery claim and the transition's narrowness were
  untested.** A case now crashes after YAML selection, disables the actor,
  and proves recovery refuses, restores the base and records the refusal; and
  proves the trigger refuses `stale_base` from `yaml_activated` and refuses
  `actor_unauthorized` written by the web role.

Acted on from the six Low findings: the transition is admitted only from an
installer, never the web role; the first recheck sits outside the candidate
validation, so an error judging the actor is never recorded as a malformed
candidate; the restore brackets its file step with the connection check as
the abort path does; and the task map states the rounds. Left as is: an actor
id that is not a portal user is admitted, because fixture and system
producers record requests under such ids and the web can only ever record a
request under the signed-in portal user; and the activation hook stays an
attribute set by the installer, beside the constructor's campaign admission.

Post-fix validation: four database-free, eight PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors and the installer's own suites.

## Round 6, correction check, single-source under the second exemption

Reviewed `413b565d`, the complete diff from main `e01b52ba`, as a check of
the round-5 serialization. Codex did not answer; Claude returned two Medium
and six Low. Both Medium findings were accepted and fixed.

- **Medium: the trigger's work lock could deadlock.** An identity update
  locked its row and then waited for the work lock inside the trigger, while
  an activation held the work lock and then read that row, and a third
  transaction could close the cycle; PostgreSQL would abort one of them, and
  an aborted activation is the wedge the lock was meant to prevent. The
  identity trigger no longer takes any lock. The recheck reads the identity
  row `FOR SHARE` again, and the configuration installer gains the one
  privilege PostgreSQL requires for that, `UPDATE` on the row's `id` column,
  which the trigger's immutability and version rules make unusable for any
  actual change, the pattern the download service's policy claim already
  uses. The activation takes the work lock, then the runtime row, then the
  identity row, and no identity writer ever waits on the work lock.
  A case holds the share lock from a real installer session and proves a
  second connection's disable waits behind it until it commits. Two schema
  objects change rather than three, and the fingerprint was refreshed after
  a fresh comparison.
- **Medium: the resume rule was not gated on the request's kind.** Any
  request found at `yaml_activated` with file and database agreeing was
  recorded as `actor_unauthorized`, though only a login-policy request can
  reach that state through the installer. The rule now applies to a
  login-policy request alone; any other request in that shape raises the
  storage invariant error, since it was put there by hand.

Acted on from the six Low findings: a comment that still described the
locked read; the guide and the ledger now say the transition is admitted
from any role but the web, which is what the trigger checks; the web-role
refusal case asserts the integrity error and its message; and a behavioral
lock case replaced the assertion on the trigger's source. Left as is: the
sign-in evaluator and the installer's recheck each build their record
filter, because sharing one would couple the request-time path to the
installer's module; and the performance concern about the trigger's lock is
moot with the lock gone.

Post-fix validation: four database-free, eight PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors, the installer's own suites and the schema baseline.

## Round 7, correction check, dual-source

Reviewed `35479b62`, the complete diff from main `e01b52ba`, as a check of
the round-6 share lock. Both sources completed: Codex answered with one
validated Medium; Claude returned one Medium and eleven Low. Both Medium
findings were accepted and fixed.

- **Medium, Codex: a refusal was not durable before the restore began.** An
  activation that refused its actor rolled back and only then restored the
  base and recorded the failure, so a crash after the rollback and before
  the restore left the candidate selected at `yaml_activated`, and the next
  pass, through recovery, judged the actor afresh: re-enabled by then, the
  refused request applied. The activation transaction now records the
  refusal itself, under the very lock that judged it, and only then is the
  base restored; a crash before that restore leaves a failed request's
  candidate selected, which the next install of any request restores first,
  since the refused request is terminal. The round-5 rule that recorded a
  refusal from an agreeing `yaml_activated` state is gone with the window
  that needed it. A case crashes between the recorded refusal and the
  restore, re-enables the actor, and proves the refusal stands and the next
  request's install restores the base and then applies.
- **Medium, Claude: the guide stated the lock order backwards.** It said
  identity row then work lock; the activation takes the work lock, then the
  runtime row, then the identity row, and the no-deadlock argument rests on
  no identity writer ever waiting on the work lock. The guide and the
  round-6 entry say so.

Acted on from the eleven Low findings: the module docstring and two guide
paragraphs are re-flowed; the schema audit section sits after the design
subsections as the other increment guides place it; the installer docstring
names the one role holding the column grant, since the offline roles
install only requests without a portal actor; and a case proves the column
grant unusable for an update that changes nothing under the real role. Left
as is: a request whose actor row is missing is still admitted, because
fixture and system producers record requests under such ids and the web can
only ever record the signed-in portal user; the refusing side of the
round-5 resume rule needs no case now that the rule is gone.

Post-fix validation: four database-free, eight PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors, the installer's own suites and the schema baseline.

## Round 8, correction check, single-source under the second exemption

Reviewed `57c07c67`, the complete diff from main `e01b52ba`, as a check of
the round-7 durable refusal. Codex did not answer; Claude, in two shards,
returned two High, two Medium and eleven Low. All four validated findings
were accepted and fixed.

- **High: the restore was left to a request the queue would never select.**
  A crash between the recorded refusal and the restore left the refused
  request terminal with its candidate still selected, and the installer's
  queue selects only resumable requests, while the web refuses every Admin
  page and cannot record a new request while file and database disagree; so
  unless another request happened to be queued already, the deployment
  stayed incoherent until an operator intervened, a state the round-5
  design, which left the request resumable, did not have. An installer pass
  that finds its queue empty now finishes the restore with no request in
  hand, through the service's admitted `restore_refused`, under the
  installation lock.
- **Medium: an abort-journaled request could not reach the restore.** The
  restore ran after the abort recoveries, which refuse a manifest naming
  neither that request's base nor its candidate, so a request carrying a
  campaign abort journal was wedged in the same window. The restore now runs
  first.
- **Medium: the crash case proved recovery only through a request queued
  before the crash.** The case now crashes with an empty queue, proves the
  queue selects nothing and the Portal users page answers 503, runs the
  requestless restore under the real installer role, and proves the page
  answers again and a later request installs. A unit case proves the
  installer loop runs the restore on an empty pass and only the request
  otherwise, and the service suite proves the pass changes nothing while
  file and database agree.

The eleven Low findings repeated earlier dispositions or concerned wording
already corrected.

Post-fix validation: four database-free, eight PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors, the installer's own suites, the runtime process suite and the
schema baseline.

## Round 9, correction check, single-source under the second exemption

Reviewed `4b13edc3`, the complete diff from main `e01b52ba`, as a check of
the round-8 idle pass. Codex did not answer; Claude, in two shards,
returned three Medium and ten Low. All three were accepted and fixed.

- **Medium: the idle pass did the full restore's work every two seconds.**
  Each empty pass readmitted the service's grants, took the installation
  lock and parsed and validated the selected YAML document, where before it
  ran one queue query. The pass now reads the small manifest and the
  pointer digest first and returns while they agree; only a disagreement
  admits the service, takes the lock and restores. The restore itself
  compares the manifest reference rather than the parsed document.
- **Medium, two findings: the in-install restore was exercised only as a
  no-op.** The round-8 case restored through the idle pass alone, so the
  restore at the start of every install, placed before the abort
  recoveries, never met a stranded candidate. The case now crashes twice:
  once with a request queued before the crash, whose install restores the
  base and applies, and once with the queue empty, restored by the idle
  pass. Left as is: a case installing an abort-journaled request in that
  window, which needs a campaign intent and its admission and would prove a
  one-line ordering the guide states.

The ten Low findings repeated earlier dispositions or concerned wording.

Post-fix validation: four database-free, eight PostgreSQL and nine browser
cases passed locally, with the page's own suite, the parish and Ministry
editors, the installer's own suites, the runtime process suite and the
schema baseline.

## Round 10, correction check, single-source under the second exemption

Reviewed `0fb8b430`, the complete diff from main `e01b52ba`, as a check of
the round-9 precheck. Codex did not answer; Claude, in two shards, returned
one Medium and sixteen Low. The Medium was accepted and fixed.

- **Medium: the short-circuit was unproven.** The service suite asserted
  only that an idle pass left the manifest unchanged, which was as true
  before the precheck as after it. It now proves, with the service's
  admission and the installation lock replaced by callables that raise,
  that an idle pass with file and database agreeing calls neither, and that
  with the manifest naming another version the pass admits the service
  first and only then takes the lock.

The sixteen Low findings repeated earlier dispositions or concerned wording.

Post-fix validation: the installer's service suite, the page's PostgreSQL
suite and the runtime process suite passed locally.

## Round 11, correction check, single-source under the second exemption

Reviewed `9d2e2b80`, the complete diff from main `e01b52ba`, as a check of
the round-10 case. Codex did not answer; Claude, in two shards, returned
the same Medium twice and seven Low. It was accepted and fixed.

- **Medium: the case's undo reached beyond its own patch.** The blanket
  `monkeypatch.undo()` after the short-circuit block also reverted the
  service-admission replacement made earlier in the case, so the assertion
  that follows, a web-profile configuration refused by the service, passed
  for an unrelated reason, host mount validation, rather than the role
  guard it proves. The lock patch is now scoped to the block with
  `monkeypatch.context()`, and the earlier patch stays in force.

The seven Low findings repeated earlier dispositions or concerned wording.

Post-fix validation: the installer's service suite, the page's PostgreSQL
suite and the runtime process suite passed locally.

## Round 12, correction check, single-source under the second exemption

Reviewed `9d455182`, the complete diff from main `e01b52ba`, as a check of
the round-11 scoping. Codex did not answer; Claude, in two shards, returned
no finding above the validation cutoff: eleven Low, each repeating an
earlier disposition or concerning wording. Nothing to fix; the branch is
the candidate for protected delivery.
