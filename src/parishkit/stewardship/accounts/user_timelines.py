"""What happened with one Admin portal user: the user Timeline's reads (#1866).

``users/<user>/timeline/`` (``user_timeline_views``) answers, for an
Administrator, "did this person sign in, what did they do, which role did
they have when, and what did we email them?" for one user of the Users page
(admin-portal spec, "User timeline"). ``<user>`` is the user's stable record
id (``AddressRule.record_id``), never the address. It is the person
counterpart of a Family's Timeline (``reports.timelines``), whose ``Event``
lines, ``narrow`` filter and When sort it reuses, so both tables read alike.

A user is one address with a Name and a role in each configuration version
that holds the record. Its lines come from four sources, each one read:

- sign-ins and sign-outs: the audit records of the Users page's Sign-in
  activity types (``audit.log_rows.ACTIVITY_TYPES["sign_in"]``) whose actor
  is one of the address's Google accounts (``PortalUser`` rows);
- Admin actions: every other audit record by those accounts, worded with
  its System logs sentence (``audit.log_descriptions.describe``), except
  page views and lookups (``log_rows.PAGE_READS``), which #1570's slice 3
  adds as a kind of their own;
- role changes: the record's role in each activated configuration, in
  activation order;
- emails: every email whose intended recipients include the address,
  through the Administrator-only definer read of frozen migration 0116
  (``jobs.delivery_reads.address_deliveries``), then the messages by id.

The ``*_lines`` and ``role_changes`` functions are pure, so the wording and
the order are unit tested without a database. The reads run in the page's
read-only snapshot and take no lock. The audit records are read through
``audit_event_actor_time`` (frozen migration 0152), the emails by id. The
role history reads the record's rows by ``record_id``, which no index
leads with (the unique key leads with the configuration): one row per
user per configuration version is a few thousand rows at parish scale,
so the scan is cheap and #1866 adds no index for it.

Sign-ins, actions and emails follow the address (its Google accounts and
the emails meant for it), while role changes follow the record: an
address deleted and added again is a new user whose timeline also shows
the address's earlier sign-ins, actions and emails.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.audit.log_descriptions import describe
from parishkit.stewardship.audit.log_rows import PAGE_READS
from parishkit.stewardship.audit.models import AuditEvent
from parishkit.stewardship.jobs.delivery_metadata import LISTED_PURPOSES, OUTCOMES
from parishkit.stewardship.reports.timelines import Event

from .policy_models import AddressRule, PortalUser
from .runtime_models import ConfigurationActivation, SystemConfiguration
from .user_rows import ROLE_LABELS
from .users_upgrade import highest_role

# The Kind filter's choices for a user's timeline, in menu order; "all"
# shows every line. A Family's timeline has its own (``reports.timelines``).
KINDS = {
    "all": _("All"),
    "sessions": _("Sign-ins and sign-outs"),
    "actions": _("Admin actions"),
    "roles": _("Role changes"),
    "emails": _("Emails"),
}
# An Administrator signing out someone else's session on Signed-in sessions
# is in System logs' Sign-in activity group, but on the acting
# Administrator's timeline it is something they did, not their own sign-in.
SIGNED_OUT_OTHER = "admin_session_signed_out"
# Each Sign-in activity type, as its line reads; a guard test keeps these
# keys equal to the group's types (less the one above).
SESSION_WORDS = {
    "admin_login": _("Signed in"),
    "admin_step_up": _("Confirmed Google sign-in again"),
    "admin_reauthenticated": _("Earlier session ended"),
    "admin_logout": _("Signed out"),
    "admin_timeout": _("Signed out after inactivity"),
    "admin_revoked": _("Session ended"),
    "admin_privileges_changed": _("Session replaced"),
    "admin_login_refused": _("Sign-in refused"),
    "admin_login_denied": _("Sign-in refused"),
}
# The detail some session lines carry, in the System logs sentences' terms.
SESSION_DETAILS = {
    "admin_reauthenticated": _("replaced by a new sign-in"),
    "admin_revoked": _(
        "access was removed, all sessions were signed out, or an "
        "Administrator signed it out"
    ),
    "admin_privileges_changed": _("the roles changed"),
    "admin_login_refused": _(
        "no role for this Google account, the account is disabled, or access "
        "was removed during the sign-in"
    ),
}
# An Admin action's recorded outcome, when it is not plain success.
OUTCOME_WORDS = {"denied": _("Refused"), "failed": _("Failed")}
# Each outbox purpose as an email line names it (``OutboxMessage.purpose``).
EMAIL_NAMES = {
    "initial": _("Invitation"),
    "reminder": _("Reminder"),
    "receipt": _("Submission receipt"),
    "family_test": _("Chosen-Family test email"),
    "daily_digest": _("Daily Administrator report"),
    "weekly_digest": _("Weekly Administrator report"),
    "operational": _("Operational notice"),
    "security_event": _("Security notice"),
    "leader_email": _("Ministry leader email"),
}
# The outbox fields an email line needs.
EMAIL_FIELDS = ("id", "purpose", "mode", "state", "created_at", "finished_at")


@dataclass(frozen=True)
class Person:
    """Who the page is about: the user's record and, if gone, when it went.

    ``role`` is the user's role now (a ``ROLE_LABELS`` key), or None for a
    former user, whose ``email`` and ``name`` are from the latest
    configuration that held the record and ``removed_at`` is when the
    configuration that no longer holds it was activated.
    """

    id: UUID
    email: str
    name: str | None = None
    role: str | None = None
    removed_at: datetime | None = None

    @property
    def display(self):
        """The heading's name: the Name, or the address when it has none."""
        return self.name or self.email

    @property
    def role_label(self):
        """The role in words, or "" for a former user."""
        return ROLE_LABELS.get(self.role, "")


@dataclass(frozen=True)
class Summary:
    """The summary's values: the last sign-in and email, and Ministries led.

    ``last_email`` is the newest email line that was not cancelled (a
    cancelled email was never sent, as on a Family's Timeline);
    ``ministries`` are the names of the Ministries the person leads now.
    """

    last_sign_in: datetime | None = None
    last_email: Event | None = None
    ministries: tuple = ()


@dataclass(frozen=True)
class UserTimeline:
    """One user's page: the person, the summary, every line and the accounts.

    ``accounts`` are the address's Google accounts as ``{"id",
    "disabled"}`` in id order, which the page's Sign-in activity controls
    post to System logs.
    """

    person: Person
    summary: Summary
    events: tuple = ()
    accounts: tuple = ()


def rule_role(role, roles):
    """A configuration's user row's role: a user's, or an older rule's highest.

    An older exact-address rule (before the users upgrade, #952) has no
    ``role``; its highest granted role stands for it, and a rule granting
    none (an explicit deny) is no user (None).
    """
    return role or highest_role(roles or ())


def role_changes(activations, roles):
    """The role lines: the record's role as each configuration activated.

    ``activations`` are ``(configuration_id, instant)`` in activation order
    and ``roles`` maps a configuration id to the record's role there (a
    configuration without the record is missing). A line is written where
    the role differs from the previous activation's: "Became a user",
    "Role changed" or "Removed as a user". A Name change is not a line.
    """
    lines, before = [], None
    for configuration, at in activations:
        role = roles.get(configuration)
        if role == before:
            continue
        if before is None:
            lines.append(Event(at, _("Became a user"), ROLE_LABELS[role], kind="roles"))
        elif role is None:
            lines.append(Event(at, _("Removed as a user"), kind="roles"))
        else:
            detail = _("%(before)s to %(after)s") % {
                "before": ROLE_LABELS[before],
                "after": ROLE_LABELS[role],
            }
            lines.append(Event(at, _("Role changed"), detail, kind="roles"))
        before = role
    return lines


def audit_lines(rows):
    """The sign-in and Admin action lines of ``(instant, type, outcome)`` rows.

    A Sign-in activity type reads as its ``SESSION_WORDS`` line; any other
    type is an Admin action whose line is its System logs sentence, with
    "Refused" or "Failed" as the detail when that is its recorded outcome.
    """
    # A sign-in ends the browser's earlier session in the same transaction,
    # so both records share an instant and their random ids would order them
    # by chance: the earlier session's end is built first, so it reads as
    # happening first in both sort directions (as on a Family's Timeline).
    rows = sorted(rows, key=lambda row: (row[0], row[1] == "admin_login"))
    lines = []
    for at, event_type, outcome in rows:
        if event_type in SESSION_WORDS:
            lines.append(
                Event(
                    at,
                    SESSION_WORDS[event_type],
                    SESSION_DETAILS.get(event_type, ""),
                    kind="sessions",
                )
            )
            continue
        lines.append(
            Event(
                at,
                describe(event_type, {"outcome": outcome}),
                OUTCOME_WORDS.get(outcome, ""),
                kind="actions",
            )
        )
    return lines


def email_lines(rows):
    """One line per email row (``EMAIL_FIELDS``): its name, outcome and mode.

    The instant is its outcome's (``finished_at``), or when it was planned
    while it is still on its way, as on a Family's Timeline. A Testing
    email is tagged, since it went to the Testing recipient. Only an email
    Sent mail lists (``LISTED_PURPOSES``) links its Mail message page; any
    other kind has no page that opens it yet.
    """
    lines = []
    for row in rows:
        detail = str(OUTCOMES.get(row["state"], _("Unknown status")))
        if row["mode"] == "testing":
            detail = f"{detail} · {_('Testing')}"
        lines.append(
            Event(
                row["finished_at"] or row["created_at"],
                EMAIL_NAMES.get(row["purpose"], _("Email")),
                detail,
                row["id"] if row["purpose"] in LISTED_PURPOSES else None,
                kind="emails",
            )
        )
    return lines


def ordered(lines):
    """Every line oldest first, each ranked by its place in that order.

    ``sorted`` is stable, so lines at one instant keep the order they were
    built in (role changes, then sign-ins and actions, then emails), and
    the rank lets the shared When sort reverse it exactly.
    """
    lines = sorted(lines, key=lambda line: line.at)
    return tuple(replace(line, rank=rank) for rank, line in enumerate(lines))


def summarize(rows, emails, ministries=()):
    """The summary from the audit rows, the email rows and the Ministries."""
    sign_ins = [at for at, event_type, _outcome in rows if event_type == "admin_login"]
    # The newest line of an email not cancelled: by the instant its line
    # shows (its outcome's, or when it was planned while on its way), so the
    # summary names the email the table lists newest.
    sent = sorted(
        email_lines(row for row in emails if row["state"] != "cancelled"),
        key=lambda line: (line.at, str(line.message_id)),
    )
    return Summary(
        max(sign_ins) if sign_ins else None,
        sent[-1] if sent else None,
        tuple(sorted(ministries, key=str.casefold)),
    )


def read_person(user_id):
    """The user's record now (or last) and its role in each configuration.

    Returns ``(person, activations, roles)``, the last two as
    ``role_changes`` takes them. Only activated configurations count. A
    record no activated configuration holds raises ``LookupError``.
    """
    rows = {
        row["configuration_id"]: row
        for row in AddressRule.objects.filter(record_id=user_id).values(
            "configuration_id", "email", "name", "role", "roles"
        )
    }
    activations = list(
        ConfigurationActivation.objects.order_by("sequence").values_list(
            "configuration_id", "created_at"
        )
    )
    roles = {
        configuration: role
        for configuration, row in rows.items()
        if (role := rule_role(row["role"], row["roles"]))
    }
    held = [index for index, (key, _at) in enumerate(activations) if key in roles]
    if not held:
        raise LookupError("No configuration holds this user.")
    active = SystemConfiguration.objects.values_list(
        "active_configuration_id", flat=True
    ).get()
    last = activations[held[-1]][0]
    row = rows[last]
    current = active in roles
    removed = None
    if not current and held[-1] + 1 < len(activations):
        removed = activations[held[-1] + 1][1]
    person = Person(
        user_id,
        row["email"],
        row["name"],
        roles[active] if current else None,
        removed,
    )
    return person, activations, roles


def audit_records(accounts):
    """The audit read: ``(instant, type, outcome)`` of the accounts' records.

    Oldest first, without page views, through ``audit_event_actor_time``
    (frozen migration 0152). Only each record's recorded ``outcome`` is
    taken from its context row, never the rest of the context.
    """
    return (
        AuditEvent.objects.filter(actor_id__in=accounts)
        .exclude(event_type__in=PAGE_READS)
        .order_by("created_at", "id")
        .values_list("created_at", "event_type", "auditcontext__context__outcome")
    )


def read_audit(accounts):
    """Every audit record by the accounts (``audit_records``), as a list."""
    return list(audit_records(accounts)) if accounts else []


def read_emails(address, actor_id):
    """Every email meant for ``address``, through the Administrator-only read.

    ``actor_id`` is the reading Administrator's portal user, which the
    definer checks; a refusal raises ``PermissionError``.
    """
    from parishkit.stewardship.jobs.delivery_reads import address_deliveries
    from parishkit.stewardship.jobs.outbox_models import OutboxMessage

    ids = address_deliveries(address, actor_id)
    if not ids:
        return []
    return list(
        OutboxMessage.objects.filter(pk__in=ids)
        .order_by("created_at", "id")
        .values(*EMAIL_FIELDS)
    )


def read_ministries(accounts):
    """The names of the Ministries the accounts lead now through ParishSoft.

    Each enabled account's scope is the shared leader scope
    (``policy.leader_scope``), named from the current ParishSoft catalog.
    """
    from .leader_import import ministry_names
    from .policy import leader_scope

    led = set()
    for account in accounts:
        if not account["disabled"]:
            led |= leader_scope(account["id"])
    return tuple(ministry_names(led).values()) if led else ()


def read_timeline(user_id, actor_id):
    """One user's page: the person, summary, lines and Google accounts.

    Runs inside the caller's read-only snapshot. ``actor_id`` is the
    reading Administrator, for the email read. An unknown user raises
    ``LookupError``. Ministries are read only for a current user: a former
    user leads nothing here.
    """
    person, activations, roles = read_person(user_id)
    accounts = tuple(
        {"id": row["id"], "disabled": row["disabled"]}
        for row in PortalUser.objects.filter(email=person.email)
        .order_by("id")
        .values("id", "disabled")
    )
    rows = read_audit([account["id"] for account in accounts])
    emails = read_emails(person.email, actor_id)
    ministries = read_ministries(accounts) if person.role else ()
    lines = [
        *role_changes(activations, roles),
        *audit_lines(rows),
        *email_lines(emails),
    ]
    return UserTimeline(
        person, summarize(rows, emails, ministries), ordered(lines), accounts
    )
