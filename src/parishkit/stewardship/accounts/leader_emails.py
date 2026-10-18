"""Email Ministry leaders (#1592): the Users page's by-hand leader emails.

An Administrator chooses Ministry leader users on the Users page, reviews
what each one's email will say, and applies it (admin-portal specification,
"Email Ministry leaders"). Nothing here sends anything: Apply records one
``LeaderEmailRequest``, which the background engine prepares and mail
dispatch sends (``jobs.leader_email_owner``). The page and, later, the
command line (#1622) share these functions:

- ``leader_rows`` adds each Ministry leader user's Last emailed outcome to
  the Users page's own user rows, from the recipient rows' addresses and
  their outbox messages' states, never their content.
- ``read_facts`` reads every Ministry leader user's campaign Ministries and
  open request counts through the ``stewardship_leader_emails_v1`` definer,
  which answers only a live Administrator session, takes no address and
  records its own count-only audit event.
- ``plan_emails`` decides, without I/O, who is emailed and who is skipped.
- ``request_emails`` records the reviewed request once; replaying the same
  review returns the first request, never a second send. A request whose
  Ministries or counts, worked out again by its guard, differ from those
  reviewed (``facts_digest``) is refused, so the email states what the
  Administrator saw.
"""

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError, connection, transaction
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.jobs.delivery_metadata import STATE_LABELS
from parishkit.stewardship.jobs.leader_email_content import (
    LeaderMinistry,
    counts_text,
    recorded_ministries,
)
from parishkit.stewardship.jobs.leader_email_models import (
    REQUEST_LIMIT,
    LeaderEmailRecipient,
    LeaderEmailRequest,
)
from parishkit.stewardship.observability import current_correlation
from parishkit.stewardship.web.tables import Sorting

from .user_changes import LOOKUP_REFUSED

# The only role these emails go to (Administrator decision on #1592).
ROLE = "ministry_leader"
# How the definer words its session refusal (SQLSTATE 42501, which a login
# without EXECUTE also gets, so the words tell the two apart).
REFUSAL = "Leader email review requires a live Administrator session"
NEVER = _("Never")

SKIP_REASONS = {
    "not_leader": _("No longer a Ministry leader user"),
    "no_ministry": _("Leads none of this campaign's Ministries in ParishSoft"),
}

# The Email Ministry leaders table's sort: Name by default, then Email, Last
# successful sign-in and Last emailed (newest first on the first choice).
EMAIL_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["name"].casefold(),
        "email": lambda row: row["email"],
        "last_login": lambda row: row["last_login"],
        "last_emailed": lambda row: row["last_emailed"],
    },
    default="name",
    descending_first={"last_login", "last_emailed"},
)


class LeaderEmailsRefused(PermissionError):
    """The definer refused the session: not a live Administrator's."""


class LeaderEmailsChanged(Exception):
    """What Apply would record differs from what was reviewed; nothing is."""


def leader_rows(rows):
    """The Ministry leader users of the Users page's rows, with Last emailed.

    ``rows`` are ``user_reads.user_list``'s. Each result adds
    ``last_emailed`` (when the latest request that emailed the address was
    applied, or None) and ``emailed_label``: that email's delivery state in
    Outgoing mail's words, "Still sending (queued)" while the request is not
    prepared yet, or "Never". Two queries: every request's addresses, newest
    first (one row per Apply, so the table stays small), and the latest
    requests' recipient rows with their messages' states. A leader user with
    no Name (such as one converted from an older sign-in rule) is listed
    under their email instead, so Email all never skips them silently.
    """
    leaders = [
        row | {"name": row["name"] or row["email"]}
        for row in rows
        if row["role"] == ROLE
    ]
    wanted = {row["email"] for row in leaders}
    latest = {}
    for request in LeaderEmailRequest.objects.order_by("-created_at", "-pk").values(
        "id", "created_at", "addresses"
    ):
        for address in wanted.intersection(request["addresses"]):
            latest.setdefault(address, request)
    states = {
        (row["cohort__request_id"], row["address"]): row["outbox__state"]
        for row in LeaderEmailRecipient.objects.filter(
            cohort__request_id__in={request["id"] for request in latest.values()},
            address__in=list(latest),
        ).values("cohort__request_id", "address", "outbox__state")
    }
    shaped = []
    for row in leaders:
        request = latest.get(row["email"])
        if request is None:
            emailed, label = None, NEVER
        else:
            state = states.get((request["id"], row["email"]), "pending")
            emailed, label = request["created_at"], STATE_LABELS.get(state, NEVER)
        shaped.append(row | {"last_emailed": emailed, "emailed_label": label})
    return shaped


@dataclass(frozen=True)
class LeaderFacts:
    """One Ministry leader user's campaign Ministries with their open counts."""

    email: str
    ministries: tuple

    @property
    def lines(self):
        """Each Ministry's name and counts, as the email words them."""
        return [
            {"name": item.name, "counts": counts_text(item)} for item in self.ministries
        ]


def read_facts(session_key):
    """Every Ministry leader user's facts, for this Admin session, by address.

    Calls the definer in its own savepoint (it writes its audit event). A
    session the definer refuses raises ``LeaderEmailsRefused``; any other
    database failure, a login without EXECUTE included, propagates.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(
                "SELECT public.stewardship_leader_emails_v1(%s, %s)::text",
                [session_key, current_correlation()],
            )
            (value,) = cursor.fetchone()
    except DatabaseError as error:
        cause = error.__cause__
        if getattr(cause, "sqlstate", None) == LOOKUP_REFUSED and (
            getattr(getattr(cause, "diag", None), "message_primary", None) == REFUSAL
        ):
            raise LeaderEmailsRefused("Leader email review refused.") from None
        raise
    return {
        item["email"]: LeaderFacts(
            item["email"],
            tuple(LeaderMinistry.from_fact(value) for value in item["ministries"]),
        )
        for item in json.loads(value)
    }


def facts_digest(ministries):
    """A digest of what the emails state: each address's Ministries and counts.

    ``ministries`` maps each emailed address to its ``LeaderMinistry`` tuple,
    as ``read_facts`` (the review) and ``recorded_ministries`` (the request
    the guard recorded at Apply) both give them, so the two compare equal
    exactly when every email would say the same thing.
    """
    shape = [
        [email, [[m.duid, str(m.name), m.join, m.leave] for m in ministries[email]]]
        for email in sorted(ministries)
    ]
    return hashlib.sha256(json.dumps(shape).encode()).hexdigest()


@dataclass(frozen=True)
class EmailPlan:
    """Who one Apply emails, who is skipped and why, and how many remain.

    ``emailed`` lists ``{"email", "name", "facts"}`` in Name order, at most
    ``REQUEST_LIMIT``; ``skipped`` lists ``{"email", "reason"}``;
    ``remaining`` counts chosen leaders past the limit.
    """

    emailed: list
    skipped: list
    remaining: int

    @property
    def addresses(self):
        """The emailed addresses, in the order the request records them."""
        return sorted(item["email"] for item in self.emailed)

    @property
    def digest(self):
        """``facts_digest`` of the emailed leaders' reviewed Ministries."""
        return facts_digest(
            {item["email"]: item["facts"].ministries for item in self.emailed}
        )


def plan_emails(leaders, facts, chosen=None):
    """Plan emailing the chosen leaders (every Ministry leader user when None).

    ``leaders`` are ``leader_rows``' rows (the current Ministry leader users);
    ``facts`` is ``read_facts``' answer; ``chosen`` is the addresses the
    Administrator ticked, already trimmed and lowercased. An address that is
    no Ministry leader user now, or whose user leads none of the campaign's
    Ministries, is skipped and named.
    """
    named = {row["email"]: row["name"] for row in leaders}
    wanted = list(named) if chosen is None else chosen
    emailed, skipped = [], []
    for email in dict.fromkeys(wanted):
        found = facts.get(email)
        if email not in named or found is None:
            skipped.append({"email": email, "reason": "not_leader"})
        elif not found.ministries:
            skipped.append({"email": email, "reason": "no_ministry"})
        else:
            emailed.append({"email": email, "name": named[email], "facts": found})
    emailed.sort(key=lambda item: (item["name"].casefold(), item["email"]))
    remaining = max(len(emailed) - REQUEST_LIMIT, 0)
    return EmailPlan(emailed[:REQUEST_LIMIT], skipped, remaining)


def request_emails(
    actor_id,
    *,
    key,
    addresses,
    configuration_id,
    campaign_id,
    mode,
    testing_recipient,
    reviewed=None,
):
    """Record one reviewed request; a replay returns the first one.

    The request's database guard checks the Administrator, the reviewed
    configuration, campaign and mode, and each address again, and records
    each leader's facts and the audit event itself. ``reviewed`` is the
    review's ``EmailPlan.digest``: when given, facts the guard worked out
    differently (a ParishSoft refresh changed a leader's Ministries or a
    count changed since the review) raise ``LeaderEmailsChanged`` and the
    request is rolled back. Returns ``(request, created)``. A replay bound
    to anything else raises ``LeaderEmailsChanged`` too.
    """
    if not isinstance(key, UUID) or not isinstance(actor_id, UUID):
        raise ValueError("Leader emails require canonical identities.")
    with work_transaction():
        previous = LeaderEmailRequest.objects.filter(pk=key).first()
        if previous is not None:
            if (
                previous.actor_id != actor_id
                or sorted(previous.addresses) != sorted(addresses)
                or previous.configuration_id != configuration_id
            ):
                raise LeaderEmailsChanged("Leader email request is already bound.")
            return previous, False
        request = LeaderEmailRequest.objects.create(
            id=key,
            actor_id=actor_id,
            correlation_id=key,
            configuration_id=configuration_id,
            campaign_id=campaign_id,
            mode=mode,
            testing_recipient=testing_recipient,
            addresses=list(addresses),
            recipients=[],
            recipient_count=len(addresses),
        )
        # The guard filled in the facts; the INSERT returned only the key.
        request.refresh_from_db(fields=["recipients"])
        if reviewed is not None and reviewed != facts_digest(
            recorded_ministries(request.recipients)
        ):
            raise LeaderEmailsChanged("Leader email facts changed since the review.")
        return request, True
