"""What happened with one Family: its timeline and the Staff summary (#477, PR 6).

``reports/families/<family>/`` answers "what happened with this
Family?" for Administrators and "did you get my response?" for Staff. Both see
a summary: whether the Family submitted and when, the last email sent to it
(a cancelled email was never sent, so it is skipped) and whether that email
reached the mail service, and its Family code with Open form. Administrators
also see the full timeline, newest first unless the reader sorts it the other
way (the Administrator's choice): each Family email and its outcome,
the invitation skipped because the Family had already responded (in place of
its cancelled email), each sign-in, each form open, getting past the first
step and the furthest step reached, and each submission with its receipt
outcome. Staff
get the summary only (the Administrator's decision of 2026-10-04): no
timeline, no Mail message links, nothing about any other Family, and the
records only the timeline needs are not even read for them.

The page is about one Family in one mode, as the response lists are:
Production by default, or one rehearsal epoch's Testing records. Family
emails, submissions, form opens and the engagement record carry their mode
(and a Testing epoch); a sign-in is a ``family_login`` audit event, which
carries neither, so it is attributed to the runtime mode in force at its
instant (``RuntimeTransition``), and a Testing view keeps only the sign-ins
within the epoch's lifetime. Testing receipts carry no epoch either; they are
found through the submissions they answer.

Everything here except the ``read_*`` functions is pure, so the summary and
the event order are unit tested without a database. The reads are small, run
inside the report's read guard and take no lock. The emails, baselines and
submissions are read through their ``family_id`` indexes and the skips
through the campaign's invitation schedules. Sign-ins are not indexed by
Family: they are read through the audit's ``(event_type, created_at)`` index,
which scans every ``family_login`` event and keeps this Family's (a few
thousand rows per campaign at launch scale).
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.campaigns.credential_models import RehearsalEpoch
from parishkit.stewardship.campaigns.engagement_models import FamilyEngagement
from parishkit.stewardship.campaigns.runtime_models import RuntimeTransition
from parishkit.stewardship.jobs.delivery_metadata import OUTCOMES
from parishkit.stewardship.jobs.send_history import reminder_numbers
from parishkit.stewardship.responses.models import FamilyFormBaseline
from parishkit.stewardship.web.tables import Sorting

from .response_metrics import MODES

# The Family form's steps (``PRESENCE_SECTIONS``), as a reader names them.
STEPS = {
    "welcome": _("Welcome"),
    "census": _("Census"),
    "members": _("Family members"),
    "ministry": _("Ministry stewardship"),
    "financial": _("Financial stewardship"),
    "closing": _("Closing page"),
    "additional": _("Additional information"),
    "review": _("Review"),
}
# The timeline is a shared Admin table with one sortable column, When:
# newest first by default (the Administrator's decision on #590), and the
# heading reverses it in place. Rows at the same instant keep the order
# ``events`` builds them in, in both directions (the sort is stable).
TIMELINE_SORTING = Sorting.by_column(
    {"when": lambda event: event.at}, default="-when", descending_first={"when"}
)
# Whether campaign email can reach the Family, by the campaign record's
# ``deliverability_reason`` (``source/families.py`` and the refusal trigger).
REACH = {
    "deliverable": _("Yes"),
    "no_eligible_email": _("No: no usable email address for a Family head"),
    "provider_suppressed": _("No: the mail service refused every address"),
    "ineligible": _("No: not part of this campaign's mailing"),
    "population_pending": _("Not known yet: ParishSoft data is still loading"),
}

# The Family's invitation, reminder and chosen-Family test emails in the mode
# (a Testing email names its rehearsal epoch; Production ones none), each with
# its occurrence and the schedule it belongs to (an invitation or reminder's
# outbox identity is its occurrence), plus the receipts of the Family's
# submissions in the mode, found through the submissions (a receipt carries
# no epoch). Read by the outbox's and the submissions' family_id indexes.
_EMAILS = """
SELECT m.id, m.purpose, m.state, m.created_at, m.finished_at, o.definition_id,
    o.id
FROM stewardship_outbox_message m
LEFT JOIN stewardship_schedule_occurrence o
    ON o.id=m.semantic_key AND m.purpose IN ('initial','reminder')
WHERE m.family_id=%(family)s AND m.campaign_id=%(campaign)s
  AND m.mode=%(mail_mode)s AND m.purpose IN ('initial','reminder','family_test')
  AND m.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
UNION ALL
SELECT m.id, m.purpose, m.state, m.created_at, m.finished_at, NULL, NULL
FROM stewardship_submission s
JOIN stewardship_submission_receipt r ON r.submission_id=s.id
JOIN stewardship_outbox_message m ON m.id=r.outbox_id
WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s
  AND s.mode=%(response_mode)s
  AND s.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
"""
# The Family's submissions in the mode, oldest first, each with its receipt
# outcome: queued (the receipt email is among _EMAILS) or no deliverable
# recipient (no receipt was sent).
_SUBMISSIONS = """
SELECT s.id, s.submitted_at, r.disposition, r.outbox_id
FROM stewardship_submission s
LEFT JOIN stewardship_submission_receipt r ON r.submission_id=s.id
WHERE s.family_id=%(family)s AND s.campaign_id=%(campaign)s
  AND s.mode=%(response_mode)s
  AND s.rehearsal_epoch_id IS NOT DISTINCT FROM %(epoch)s
ORDER BY s.submitted_at, s.id
"""
# When a planned invitation to the Family was skipped because it had already
# responded, as the funnel counts it: the immutable occurrence transition,
# within the scope's lifetime (occurrences carry no epoch). Read through the
# campaign's invitation schedules, as the funnel's ``skipped`` is. The
# occurrence names the cancelled email the skip stands for.
_SKIPS = """
SELECT t.created_at, o.id
FROM stewardship_schedule_definition d
JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id
JOIN stewardship_occurrence_transition t ON t.occurrence_id=o.id
WHERE d.campaign_id=%(campaign)s AND d.kind='initial'
  AND o.mode=%(mail_mode)s AND o.target=%(target)s
  AND t.after_state='skipped' AND t.reason='family_responded'
  AND (%(started)s::timestamptz IS NULL OR t.created_at>=%(started)s)
  AND (%(ended)s::timestamptz IS NULL OR t.created_at<%(ended)s)
ORDER BY t.created_at
"""
# When a planned reminder to the Family was skipped because the Family is in
# the campaign's Reminder WorkGroup (#861), read like ``_SKIPS``.
_WORKGROUP_SKIPS = """
SELECT t.created_at, o.id
FROM stewardship_schedule_definition d
JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id
JOIN stewardship_occurrence_transition t ON t.occurrence_id=o.id
WHERE d.campaign_id=%(campaign)s AND d.kind='reminder'
  AND o.mode=%(mail_mode)s AND o.target=%(target)s
  AND t.after_state='skipped' AND t.reason='workgroup_excluded'
  AND (%(started)s::timestamptz IS NULL OR t.created_at>=%(started)s)
  AND (%(ended)s::timestamptz IS NULL OR t.created_at<%(ended)s)
ORDER BY t.created_at
"""


@dataclass(frozen=True)
class Email:
    """One email to the Family: what it was, how it ended, and when.

    ``name`` is its plain name (Invitation, Reminder N, Submission receipt,
    Chosen-Family test email). ``created_at`` is when it was planned and
    ``finished_at`` when it got its final outcome (None while it is still on
    its way). ``occurrence`` is an invitation or reminder's schedule
    occurrence (None for a receipt or a test email).
    """

    id: UUID
    purpose: str
    name: str
    state: str
    created_at: datetime
    finished_at: datetime | None = None
    occurrence: UUID | None = None

    @property
    def at(self):
        """When it last changed: its outcome, or when it was planned."""
        return self.finished_at or self.created_at

    @property
    def outcome(self):
        """Its outcome in plain words."""
        return OUTCOMES.get(self.state, _("Unknown status"))


@dataclass(frozen=True)
class Submitted:
    """One submission and its receipt.

    ``receipt`` is the receipt email when one was queued; ``no_receipt`` says
    the Family had no email address to send one to.
    """

    id: UUID
    at: datetime
    receipt: Email | None = None
    no_receipt: bool = False


@dataclass(frozen=True)
class Engagement:
    """The engagement record's values this page shows (Administrators only).

    ``progress_at`` is when the Family got past the first step; ``furthest`` the
    furthest step reached (a ``STEPS`` key, or "") and ``furthest_at`` when;
    ``last_seen_at`` the latest instant the Family was seen on the form.
    """

    progress_at: datetime | None = None
    furthest: str = ""
    furthest_at: datetime | None = None
    last_seen_at: datetime | None = None

    @property
    def furthest_label(self):
        """The furthest step reached, in words, or "" when none was recorded."""
        return STEPS.get(self.furthest, "") if self.furthest else ""


@dataclass(frozen=True)
class Event:
    """One line of the timeline: when, what happened and any detail.

    ``message_id`` names the email a line is about, so the page can link its
    Mail message page.
    """

    at: datetime
    what: str
    detail: str = ""
    message_id: UUID | None = None


@dataclass(frozen=True)
class Summary:
    """What both roles see: submitted or not and when, and the last email."""

    submissions: tuple
    last_email: Email | None

    @property
    def count(self):
        """How many times the Family submitted in this mode."""
        return len(self.submissions)

    @property
    def first_at(self):
        """When the Family first submitted, or None."""
        return self.submissions[0].at if self.submissions else None

    @property
    def last_at(self):
        """When the Family last submitted, or None."""
        return self.submissions[-1].at if self.submissions else None


def email_name(purpose, number=None):
    """An email's plain name; ``number`` is a reminder's (None: removed)."""
    if purpose == "initial":
        return _("Invitation")
    if purpose == "reminder":
        if number is None:
            return _("Reminder")
        return _("Reminder %(number)s") % {"number": number}
    if purpose == "receipt":
        return _("Submission receipt")
    return _("Chosen-Family test email")


def summary(emails, submissions):
    """The summary: the submissions as given and the last email sent.

    ``emails`` is any iterable of ``Email``; the last email is the one
    planned last (ties broken by id, so the choice is stable) among those not
    cancelled: a cancelled email was never sent (the decision of 2026-10-04
    names the "last email sent"). The timeline still lists it.
    """
    emails = sorted(
        (email for email in emails if email.state != "cancelled"),
        key=lambda email: (email.created_at, str(email.id)),
    )
    return Summary(tuple(submissions), emails[-1] if emails else None)


def mode_at(transitions, at, current):
    """The runtime mode (``production`` or ``testing``) in force at ``at``.

    ``transitions`` are ``(created_at, before_mode, after_mode)`` in time
    order; ``current`` is the mode now, used when there are none. Before
    the first transition the mode was that transition's ``before_mode``.
    A transition at the very instant of ``at`` counts as already made.
    """
    mode = transitions[0][1] if transitions else current
    for created_at, _before, after in transitions:
        if created_at > at:
            break
        mode = after
    return mode


def scope_sign_ins(instants, transitions, current, mode, lifetime):
    """The sign-ins of one mode: those made while the system was in ``mode``.

    ``lifetime`` is ``(started, ended)`` for a Testing epoch (``ended`` None
    while it is still active) or ``(None, None)`` for Production, which keeps
    every Production sign-in.
    """
    started, ended = lifetime
    return [
        at
        for at in instants
        if mode_at(transitions, at, current) == mode
        and (started is None or at >= started)
        and (ended is None or at < ended)
    ]


def events(
    emails,
    submissions,
    *,
    skips=(),
    sign_ins=(),
    forms=(),
    engagement=None,
    workgroup_skips=(),
):
    """The Administrator's timeline: every line, oldest first.

    The page shows it newest first (``TIMELINE_SORTING``); this order is the
    tie order the shared table keeps for equal instants.

    Lines at the same instant keep the order they are built in (sign-in
    before form open before progress before submission), so a Family's own
    steps read in the order it took them. ``skips`` are ``(instant,
    occurrence)`` pairs; a skip's line replaces the cancelled email of the
    same occurrence, so one planned invitation is not listed twice.
    ``workgroup_skips`` are the same pairs for reminders skipped because the
    Family is in the campaign's Reminder WorkGroup (#861).
    """
    lines = [
        Event(at, _("Signed in"), _("or a mail scanner checked the link"))
        for at in sign_ins
    ]
    lines += [Event(at, _("Opened the form")) for at in forms]
    if engagement is not None and engagement.progress_at is not None:
        lines.append(Event(engagement.progress_at, _("Got past the first step")))
    if engagement is not None and engagement.furthest_at is not None:
        lines.append(
            Event(
                engagement.furthest_at,
                _("Reached the furthest step so far"),
                engagement.furthest_label,
            )
        )
    for submission in submissions:
        detail = _("No receipt: no email address") if submission.no_receipt else ""
        lines.append(Event(submission.at, _("Submitted a response"), detail))
    lines += [
        Event(at, _("Invitation not sent"), _("already responded"))
        for at, _occurrence in skips
    ]
    lines += [
        Event(at, _("Reminder not sent"), _("in the ParishSoft Reminder WorkGroup"))
        for at, _occurrence in workgroup_skips
    ]
    skipped = {occurrence for _at, occurrence in (*skips, *workgroup_skips)}
    lines += [
        Event(email.at, email.name, email.outcome, email.id)
        for email in emails
        if not (email.state == "cancelled" and email.occurrence in skipped)
    ]
    # sorted() is stable, so equal instants keep the order built above.
    return tuple(sorted(lines, key=lambda line: line.at))


def scope_values(scope, family_id):
    """The statements' parameters for one Family in ``scope``."""
    return {
        "campaign": scope.campaign_id,
        "family": family_id,
        "target": f"family:{family_id}",
        "epoch": scope.rehearsal_epoch_id,
        "response_mode": MODES[scope.mode][0],
        "mail_mode": MODES[scope.mode][1],
    }


def read_emails(cursor, values):
    """The Family's emails in the scope, named; receipts keyed by outbox id."""
    cursor.execute(_EMAILS, values)
    found = cursor.fetchall()
    numbers = (
        reminder_numbers(cursor, values["campaign"])
        if any(row[1] == "reminder" for row in found)
        else {}
    )
    emails = []
    for message, purpose, state, created_at, finished, definition, occurrence in found:
        name = email_name(purpose, numbers.get(definition))
        emails.append(
            Email(message, purpose, name, state, created_at, finished, occurrence)
        )
    return emails


def read_submissions(cursor, values, emails):
    """The Family's submissions in the scope, oldest first, with their receipts."""
    by_id = {email.id: email for email in emails}
    cursor.execute(_SUBMISSIONS, values)
    return [
        Submitted(
            submission,
            at,
            by_id.get(outbox),
            disposition == "no_deliverable_recipient",
        )
        for submission, at, disposition, outbox in cursor.fetchall()
    ]


def lifetime(scope):
    """``(started, ended)`` of a Testing scope's epoch; Production is unbounded."""
    if scope.rehearsal_epoch_id is None:
        return None, None
    epoch = RehearsalEpoch.objects.values("created_at", "invalidated_at").get(
        pk=scope.rehearsal_epoch_id
    )
    return epoch["created_at"], epoch["invalidated_at"]


def read_sign_ins(family_id, scope, current_mode, bounds):
    """The Family's sign-ins in the scope's mode and lifetime, oldest first.

    Sign-in audit events carry no mode, so each one is attributed to the
    runtime mode in force when it happened.
    """
    instants = list(
        AuditEvent.objects.filter(event_type="family_login", actor_id=family_id)
        .order_by("created_at")
        .values_list("created_at", flat=True)
    )
    if not instants:
        return []
    transitions = list(
        RuntimeTransition.objects.order_by("created_at", "id").values_list(
            "created_at", "before_mode", "after_mode"
        )
    )
    return scope_sign_ins(instants, transitions, current_mode, scope.mode, bounds)


def read_engagement(family_id, scope):
    """The Family's engagement record in the scope, or None."""
    row = (
        FamilyEngagement.objects.filter(
            family_id=family_id,
            mode=MODES[scope.mode][0],
            rehearsal_epoch_id=scope.rehearsal_epoch_id,
        )
        .values("first_progress_at", "furthest_section", "furthest_at", "last_seen_at")
        .first()
    )
    if row is None:
        return None
    return Engagement(
        row["first_progress_at"],
        row["furthest_section"],
        row["furthest_at"],
        row["last_seen_at"],
    )


@dataclass(frozen=True)
class Timeline:
    """One Family's records in one scope: the summary, and the full view's extras.

    ``events`` and ``engagement`` are only read for the full view; for the
    reduced (Staff) view they stay empty and None.
    """

    summary: Summary
    events: tuple = ()
    engagement: Engagement | None = None


def read_timeline(scope, family_id, *, full, current_mode="production"):
    """Read one Family's records in ``scope``; ``full`` adds the timeline.

    Runs inside the report's read guard and read-only transaction. The
    reduced view reads only the emails and submissions its summary needs;
    the full view adds the skips, sign-ins (``current_mode`` is the runtime
    mode now, for attributing them), form opens and the engagement record.
    """
    values = scope_values(scope, family_id)
    with connection.cursor() as cursor:
        emails = read_emails(cursor, values)
        submissions = read_submissions(cursor, values, emails)
        if not full:
            return Timeline(summary(emails, submissions))
        bounds = lifetime(scope)
        window = values | {"started": bounds[0], "ended": bounds[1]}
        cursor.execute(_SKIPS, window)
        skips = cursor.fetchall()
        cursor.execute(_WORKGROUP_SKIPS, window)
        workgroup_skips = cursor.fetchall()
    forms = FamilyFormBaseline.objects.filter(
        family_id=family_id,
        mode=values["response_mode"],
        rehearsal_epoch_id=scope.rehearsal_epoch_id,
    ).values_list("created_at", flat=True)
    engagement = read_engagement(family_id, scope)
    return Timeline(
        summary(emails, submissions),
        events(
            emails,
            submissions,
            skips=skips,
            sign_ins=read_sign_ins(family_id, scope, current_mode, bounds),
            forms=sorted(forms),
            engagement=engagement,
            workgroup_skips=workgroup_skips,
        ),
        engagement,
    )
