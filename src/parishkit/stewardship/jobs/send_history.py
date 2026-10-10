"""The current campaign's Family email history (#432).

A *send* is what the live progress panel (``send_progress``) follows: one
revision of one Family schedule definition (the invitation, or one reminder)
in one mode and Production cycle. Moving or editing a schedule makes a new
revision and so a new send: the earlier send keeps the emails it sent and
shows the ones the edit cancelled, and the new one starts from nothing. Each
Family counts once, by its newest occurrence of the send. Every send is
counted with the panel's own statements (``send_progress._count``), so the
history and the panel always agree.

The Outgoing mail page (``delivery_views.delivery_list``) can list exactly the
emails of one send: each Family's newest occurrence's email, the same rows
the counts read. Combined with its state filter, each linked count opens
exactly the emails it counts.

Every statement reads only tables the web login already reads for the panel
and takes no lock; callers run them in ``read_transaction``. Listing the
sends is one aggregate over the campaign's Family occurrences through the
definition index; a campaign has a handful of sends, so the list is paged in
memory, and only the shown page's sends are counted.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import NamedTuple
from urllib.parse import urlencode
from uuid import UUID

from django.db import connection
from django.utils.translation import gettext_lazy as _

# The panel's own statements and counting, shared so the two pages agree.
from .send_progress import (
    _EMAIL,
    _LATEST,
    RATE_WINDOW,
    UNREACHABLE_REASONS,
    SendProgress,
    _count,
    progress,
)

# The send's emails, as the counts read them: each Family's newest
# occurrence's email (see send_progress._LATEST).
_EMAIL_IDS = "SELECT outbox_id FROM (" + _LATEST + ") l WHERE outbox_id IS NOT NULL"
# How many of those emails are in each outbox state.
_EMAIL_STATES = (
    "WITH latest AS (" + _LATEST + ") SELECT m.state, count(*) FROM latest l "
    "JOIN LATERAL (" + _EMAIL.format("l") + ") m ON true GROUP BY m.state"
)
# Every send of the campaign that planned an email: its schedule, revision,
# mode and cycle, its scheduled time (the revision's due time), whether the
# revision is still the schedule's current one, and when its newest email
# was planned (the tiebreak, so the newest send lists first). The LATERAL
# reads each Family schedule's occurrences through the definition index, so
# other schedules' and campaigns' occurrences are never scanned.
_SENDS = (
    "SELECT d.id, x.revision_id, x.mode, x.production_cycle, d.kind, r.due_at, "
    "(x.revision_id=d.current_revision_id) IS TRUE, x.planned "
    "FROM stewardship_schedule_definition d "
    "CROSS JOIN LATERAL (SELECT o.revision_id, o.mode, o.production_cycle, "
    "max(o.created_at) AS planned FROM stewardship_schedule_occurrence o "
    "WHERE o.definition_id=d.id "
    "GROUP BY o.revision_id, o.mode, o.production_cycle) x "
    "JOIN stewardship_schedule_revision r ON r.id=x.revision_id "
    "WHERE d.campaign_id=%(campaign)s AND d.kind IN ('initial','reminder')"
)
# The campaign's current reminder schedules, in order of their current due
# time: Reminder 1, Reminder 2, … in every mode, as Dates and mail schedules lists
# them. A removed reminder has no current due time and so no number.
_REMINDERS = (
    "SELECT d.id FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_revision r ON r.id=d.current_revision_id "
    "WHERE d.campaign_id=%(campaign)s AND d.kind='reminder' "
    "ORDER BY r.due_at, d.id"
)
# One send named by a link, if it planned any email: its campaign, kind,
# scheduled time and whether its revision is still the current one. Reads
# only that schedule's occurrences, through the definition index.
_DESCRIBE = (
    "SELECT d.campaign_id, d.kind, r.due_at, (r.id=d.current_revision_id) IS TRUE "
    "FROM stewardship_schedule_definition d "
    "JOIN stewardship_schedule_revision r ON r.id=%(revision)s "
    "WHERE d.id=%(definition)s AND d.kind IN ('initial','reminder') "
    "AND EXISTS (SELECT 1 FROM stewardship_schedule_occurrence o "
    "WHERE o.definition_id=d.id AND o.revision_id=%(revision)s "
    "AND o.mode=%(mode)s AND o.production_cycle=%(cycle)s)"
)
_UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"
_KEY = re.compile(
    rf"(?P<definition>{_UUID}):(?P<revision>{_UUID})"
    r":(?P<mode>testing|production):(?P<cycle>0|[1-9][0-9]{0,17})"
)


@dataclass(frozen=True)
class SendKey:
    """One send: a Family schedule revision in one mode and Production cycle.

    ``token`` is how a URL carries it (``definition:revision:mode:cycle``),
    which ``parse`` reads back, refusing anything else.
    """

    definition: UUID
    revision: UUID
    mode: str
    cycle: int

    @property
    def token(self):
        """The ``send`` query value that selects this send on Outgoing mail."""
        return f"{self.definition}:{self.revision}:{self.mode}:{self.cycle}"

    @classmethod
    def parse(cls, text):
        """Read a ``token``; any other text is a ValueError."""
        match = _KEY.fullmatch(text) if type(text) is str else None
        if match is None:
            raise ValueError("Unknown Family email send.")
        return cls(
            UUID(match["definition"]),
            UUID(match["revision"]),
            match["mode"],
            int(match["cycle"]),
        )

    def values(self):
        """Statement parameters naming this send."""
        return {
            "definition": self.definition,
            "revision": self.revision,
            "mode": self.mode,
            "cycle": self.cycle,
        }


class ListedSend(NamedTuple):
    """One send as listed, before it is counted.

    ``number`` is the reminder's number (see ``_REMINDERS``): one per
    reminder schedule, the same in every mode and for every revision, and
    None for the invitation or a removed reminder. ``replaced`` is a send
    whose schedule was later changed or removed.
    """

    key: SendKey
    kind: str
    scheduled: datetime
    number: int | None
    replaced: bool

    @property
    def name(self):
        """The send's plain name: Invitation, Reminder N, or Reminder."""
        if self.kind == "initial":
            return _("Invitation")
        if self.number is None:
            return _("Reminder")
        return _("Reminder %(number)s") % {"number": self.number}

    @property
    def mode_label(self):
        """The send's mode, as Outgoing mail names it."""
        return _("Production") if self.key.mode == "production" else _("Testing")


@dataclass(frozen=True)
class Outcome:
    """One linked count: ``count`` Families, ``listed`` of them with an email.

    ``state`` is the outbox state Outgoing mail filters on and ``label`` its
    plain name. ``listed`` can be below ``count`` when some failed before an
    email was prepared; ``query`` is the Outgoing mail filter listing them.
    ``send`` names the send and its mode, so each link's accessible name
    says which send it lists.
    """

    label: str
    state: str
    count: int
    listed: int
    query: str
    send: str


@dataclass(frozen=True)
class SendRow:
    """One send in the history, counted by the panel's rules.

    ``listed`` is the ``ListedSend``; ``current`` is a send in the current
    mode and cycle, and ``live`` the one the progress panel shows now (see
    ``mark_live``). ``emails`` maps outbox states to how many of the send's
    emails are in them. ``earlier`` marks a Production send from before the
    campaign last returned to Testing.
    """

    listed: ListedSend
    current: bool
    earlier: bool
    send: SendProgress
    emails: dict
    live: bool = False

    @property
    def first_at(self):
        """When the send's first email was prepared."""
        return self.send.counts.started_at

    @property
    def last_at(self):
        """When the send's most recent email got its result."""
        return self.send.counts.last_settled_at

    @property
    def minutes(self):
        """Whole minutes from the first email to the latest result, or None."""
        if self.first_at is None or self.last_at is None:
            return None
        return max(0, round((self.last_at - self.first_at).total_seconds() / 60))

    def _outcome(self, label, state, count):
        """One ``Outcome``: ``count`` Families in ``state``, with its filter."""
        return Outcome(
            label,
            state,
            count,
            self.emails.get(state, 0),
            urlencode({"send": self.listed.key.token, "state": state}),
            f"{self.listed.name}, {self.listed.mode_label}",
        )

    @property
    def sent(self):
        """The send's delivered emails."""
        return self._outcome(_("sent"), "delivered", self.send.counts.sent)

    @property
    def failed(self):
        """The send's failures, some of which may have no email to list."""
        return self._outcome(_("failed"), "permanent_failure", self.send.counts.failed)

    @property
    def not_sure(self):
        """The send's emails the mail service did not confirm either way."""
        return self._outcome(
            _("uncertain"), "delivery_unknown", self.send.counts.uncertain
        )

    @property
    def cancelled(self):
        """The send's emails that were prepared and then cancelled.

        For example by a schedule change; those Families are also among
        Couldn't be emailed or Not needed, so this is not part of the total.
        """
        return self._outcome(
            _("cancelled"), "cancelled", self.emails.get("cancelled", 0)
        )

    @property
    def outcomes(self):
        """Sent, failed, not sure and cancelled, for callers that check them all."""
        return (self.sent, self.failed, self.not_sure, self.cancelled)


def reminder_numbers(cursor, campaign_id):
    """{reminder schedule id: its number} for the campaign (see ``_REMINDERS``)."""
    cursor.execute(_REMINDERS, {"campaign": campaign_id})
    return {row[0]: number for number, row in enumerate(cursor.fetchall(), start=1)}


def list_sends(campaign_id):
    """Every send of the campaign, newest first, as ``ListedSend`` tuples.

    Cheap: one aggregate plus the reminder numbers, and no send is counted
    here. A schedule's due time that planning has not reached yet has no
    row until its first email is planned (the progress panel shows it from
    its first minute).
    """
    with connection.cursor() as cursor:
        cursor.execute(_SENDS, {"campaign": campaign_id})
        found = cursor.fetchall()
        numbers = reminder_numbers(cursor, campaign_id)
    found.sort(key=lambda row: (row[5], row[7], row[1]), reverse=True)
    return [
        ListedSend(
            SendKey(definition, revision, mode, cycle),
            kind,
            scheduled,
            numbers.get(definition),
            not current,
        )
        for definition, revision, mode, cycle, kind, scheduled, current, _at in found
    ]


def count_row(campaign, listed, *, mode, paused, now):
    """Count one listed send with the panel's rules, plus its emails by state.

    ``mode`` is the system's current mode and ``paused`` whether live
    delivery is paused now; a pause applies only to sends in the current
    mode and cycle. ``live`` is left for ``mark_live``.
    """
    key = listed.key
    cycle = campaign.production_cycle if mode == "production" else 0
    values = {
        "campaign": campaign.pk,
        "since": now - RATE_WINDOW,
        "unreachable": list(UNREACHABLE_REASONS),
    } | key.values()
    with connection.cursor() as cursor:
        counts = _count(cursor, values, listed.kind, now)
        cursor.execute(_EMAIL_STATES, key.values())
        emails = dict(cursor.fetchall())
    current = (key.mode, key.cycle) == (mode, cycle)
    return SendRow(
        listed=listed,
        current=current,
        earlier=key.mode == "production" and key.cycle != campaign.production_cycle,
        send=progress(counts, paused=paused and current),
        emails=emails,
    )


def mark_live(rows, panel):
    """Mark the row the progress panel shows now, if it is on this page.

    ``panel`` is ``send_progress.read_send`` for the current mode and cycle,
    read in the same snapshot and at the same ``now`` as the rows, so the
    send it picked has exactly the same counts as its row. Only a send in
    progress is marked, and only when exactly one row matches; any other
    send in progress says so without linking to the panel, which is showing
    a different send.
    """
    if panel is None or not panel.in_progress:
        return rows
    matches = [
        index
        for index, row in enumerate(rows)
        if row.current and row.send.counts == panel
    ]
    if len(matches) != 1:
        return rows
    rows = list(rows)
    rows[matches[0]] = replace(rows[matches[0]], live=True)
    return rows


def describe(key):
    """The ``ListedSend`` for ``key``, or None when it names no Family send.

    The send may belong to any campaign: an Outgoing mail link stays valid
    after the next campaign becomes current. Reads only the named schedule's
    occurrences and the campaign's reminder schedules.
    """
    with connection.cursor() as cursor:
        cursor.execute(_DESCRIBE, key.values())
        found = cursor.fetchone()
        if found is None:
            return None
        campaign_id, kind, scheduled, current = found
        number = reminder_numbers(cursor, campaign_id).get(key.definition)
    return ListedSend(key, kind, scheduled, number, not current)


def email_ids(key):
    """The ids of the send's emails: each Family's newest occurrence's email."""
    with connection.cursor() as cursor:
        cursor.execute(_EMAIL_IDS, key.values())
        return [row[0] for row in cursor.fetchall()]
