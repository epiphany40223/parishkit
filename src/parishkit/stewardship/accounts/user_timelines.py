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

The Family part (#1867, admin-portal spec "User timeline Family part"):
the ParishSoft Families whose Family or Member contacts hold the address
(``person_identity.address_people``, frozen migration 0157) each get a
``FamilyPart`` with that Family's Timeline summary fields, read by the
Family Timeline's own readers (``reports.timelines.read_timeline`` and
``reports.timeline_views.read_identity``) in the current campaign, and
their lines join the user's, each tagged with the Family's name. They are
read only while the campaign's report admission is open, as the Family's
Timeline is.
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
    # A Family member's Family form lines (#1867); the menu offers it only
    # when there are such lines (``UserTimeline.form_lines``).
    "form": _("Family form activity"),
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
class FamilyPart:
    """One ParishSoft Family the user's address belongs to (#1867).

    ``name`` is the directory's surname-and-heads name from the current
    ParishSoft data. ``state`` says what the page can show: ``shown`` (the
    Family's summary fields, from ``identity``, ``records`` and
    ``open_form``), ``not_in_campaign`` (no current campaign holds the
    Family, so it has no Family form records) or ``unavailable`` (other
    campaign work holds the campaign's reports). ``family_id`` is the
    Family's record in the current campaign, which its Timeline link and
    Open form name; None when it has none. ``campaign_id`` is that
    campaign, for the view's audit (``record_family_views``).
    ``test_url`` is the Testing "Try the Family form as a chosen Family"
    link the Family's Timeline gives the same reader, or None.
    """

    duid: int
    name: str | None = None
    family_id: UUID | None = None
    state: str = "not_in_campaign"
    identity: object = None
    records: object = None
    open_form: tuple = (False, "")
    campaign_id: UUID | None = None
    test_url: str | None = None

    @property
    def label(self):
        """The Family's tag on its lines: its name, else its DUID."""
        return self.name or _("Family %(duid)s") % {"duid": self.duid}

    @property
    def reason_id(self):
        """The id of its Open form reason, unique among the page's Families."""
        return f"open-form-reason-{self.duid}"


@dataclass(frozen=True)
class UserTimeline:
    """One user's page: the person, the summary, every line and the accounts.

    ``accounts`` are the address's Google accounts as ``{"id",
    "disabled"}`` in id order, which the page's Sign-in activity controls
    post to System logs. ``families`` are the ``FamilyPart`` of each
    ParishSoft Family the address belongs to, in DUID order (#1867).
    """

    person: Person
    summary: Summary
    events: tuple = ()
    accounts: tuple = ()
    families: tuple = ()

    @property
    def form_lines(self):
        """Whether any Family form line is listed, so Kind offers it.

        Only a Family's own ``form`` lines (opens, steps, submissions) are
        Family form activity; its sign-ins and emails are under Sign-ins and
        sign-outs and Emails, so a Family with only those offers nothing.
        """
        return any(
            line.family_duid is not None and line.kind == "form" for line in self.events
        )


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


def read_families(address, actor_id, *, codes=False, test_link=False):
    """The ``FamilyPart`` of each ParishSoft Family the address belongs to.

    The Families come from the Administrator-only identity read
    (``person_identity.address_people``), their names from the current
    ParishSoft data, and their records in the current campaign, Production
    only, through the Family Timeline's own readers, so both pages show the
    same values. ``codes`` says the reader may see Family codes; the code
    is decrypted only then. ``test_link`` says the reader may also be
    offered the chosen-Family test page (``CONFIGURE``), as the Family's
    Timeline offers it in Testing mode. The records are read only while the campaign's
    report admission is open (as ``admit_report_read`` checks it for the
    Family's Timeline): during a restore review or other campaign work the
    part is ``unavailable``, as it is while a key rotation holds the codes.
    Statements: the identity read, the snapshot pointer and names, the
    campaign records and admission once, then the Family Timeline's reads
    per Family (usually one).
    """
    from parishkit.stewardship.accounts.campaign_family_test import (
        chosen_family_test_url,
    )
    from parishkit.stewardship.campaigns.credential_models import FamilyCampaign
    from parishkit.stewardship.campaigns.models import Campaign
    from parishkit.stewardship.reports.export_services import admit_campaign
    from parishkit.stewardship.reports.response_metrics import ResponseScope
    from parishkit.stewardship.reports.timeline_views import open_form, read_identity
    from parishkit.stewardship.reports.timelines import read_timeline as read_family
    from parishkit.stewardship.source.snapshot_models import SourceCurrent
    from parishkit.stewardship.source.snapshot_names import snapshot_family_facts
    from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

    from .cryptography import CryptographicError
    from .person_identity import address_people

    duids = list(address_people(address, actor_id))
    if not duids:
        return ()
    snapshot = SourceCurrent.objects.values_list("snapshot_id", flat=True).first()
    facts = snapshot_family_facts(snapshot, duids, NAME_NOT_RECORDED)
    # The instance, not values(): the chosen-Family test link reads it.
    system = SystemConfiguration.objects.only("mode", "current_campaign_id").get()
    campaign_id = system.current_campaign_id
    held = {}
    if campaign_id is not None:
        held = {
            family.family_duid: family
            for family in FamilyCampaign.objects.filter(
                campaign_id=campaign_id, family_duid__in=duids
            )
        }
    admitted, values, test_url = False, None, None
    if held:
        try:
            admit_campaign(campaign_id, mutating=False)
            admitted = True
        except PermissionError:
            pass
    if admitted:
        campaign = Campaign.objects.select_related("active_configuration").get(
            pk=campaign_id
        )
        values = campaign.active_configuration.values
        # Only with codes, as on the Family's Timeline: the link is the way
        # round a live code that Testing mode refuses.
        if codes and test_link:
            test_url = chosen_family_test_url(system, campaign)
    parts = []
    for duid in duids:
        name = facts[duid].name if duid in facts else None
        family = held.get(duid)
        if family is None:
            parts.append(FamilyPart(duid, name))
            continue
        if not admitted:
            parts.append(FamilyPart(duid, name, family.pk, "unavailable"))
            continue
        try:
            identity = read_identity(family, codes=codes, workgroup=values)
        except CryptographicError:
            # A key rotation holds the codes for a moment: the part reads as
            # unavailable rather than failing the whole page.
            parts.append(FamilyPart(duid, name, family.pk, "unavailable"))
            continue
        parts.append(
            FamilyPart(
                duid,
                name,
                family.pk,
                "shown",
                identity,
                read_family(
                    ResponseScope(campaign_id),
                    family.pk,
                    full=True,
                    current_mode=system.mode,
                ),
                open_form(
                    identity,
                    testing_codes=system.mode == "testing",
                    eligible=family.portal_eligible,
                ),
                campaign_id,
                test_url,
            )
        )
    return tuple(parts)


def record_family_views(timeline, actor_id, *, printed=None):
    """Record a ``family_timeline_viewed`` for each Family the page showed.

    ``printed``, for the command line, is the DUIDs of the Families whose
    lines it prints: it shows no Family summary, so a Family none of whose
    lines pass the filters was not looked at and is not recorded.

    A shown Family's panel and lines are what its own Timeline shows (its
    code included), so each is recorded as that page records a view: the
    Family's campaign record id as subject, its campaign, and the outcome
    only, so a later review sees who looked at which Family whichever page
    showed it. Runs inside the caller's audit transaction, after the
    recheck; one insert per shown Family (usually none or one).
    """
    from parishkit.stewardship.audit.schemas import Action, ActorKind, Outcome
    from parishkit.stewardship.audit.services import record_action

    for part in timeline.families:
        if part.state == "shown" and (printed is None or part.duid in printed):
            record_action(
                Action.FAMILY_TIMELINE_VIEWED,
                actor_kind=ActorKind.PORTAL_USER,
                actor_id=actor_id,
                subject_id=part.family_id,
                campaign_id=part.campaign_id,
                current_parish=True,
                context={"outcome": Outcome.SUCCEEDED},
            )


def family_lines(families):
    """Every shown Family's Timeline lines, each tagged with its Family.

    In each Family's own order (its ``rank``), so lines at one instant keep
    the order the Family Timeline gives them when ``ordered`` re-ranks the
    merged list.
    """
    return [
        replace(line, tag=part.label, family_duid=part.duid)
        for part in families
        if part.state == "shown"
        for line in part.records.events
    ]


def read_timeline(user_id, actor_id, *, codes=False, test_link=False):
    """One user's page: the person, summary, lines, accounts and Families.

    Runs inside the caller's read-only snapshot. ``actor_id`` is the
    reading Administrator, for the email and identity reads; ``codes`` says
    the reader may see Family codes and ``test_link`` the chosen-Family
    test page (``read_families``). An unknown user
    raises ``LookupError``. Ministries are read only for a current user: a
    former user leads nothing here. A Family email that also went to the
    user's address is listed once, as the Family's line, which names it as
    the Family Timeline does ("Reminder 2"); the summary's Last email still
    counts it.
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
    families = read_families(person.email, actor_id, codes=codes, test_link=test_link)
    tagged = family_lines(families)
    listed = {line.message_id for line in tagged if line.message_id}
    lines = [
        *role_changes(activations, roles),
        *audit_lines(rows),
        *email_lines(row for row in emails if row["id"] not in listed),
        *tagged,
    ]
    return UserTimeline(
        person,
        summarize(rows, emails, ministries),
        ordered(lines),
        accounts,
        families,
    )
