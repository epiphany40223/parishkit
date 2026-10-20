"""Explicit operational read projection; no rendered mail or credential reads."""

import json
import re
from uuid import UUID

from django.db import connection
from django.db.models import (
    Count,
    F,
    Func,
    JSONField,
    OuterRef,
    Q,
    Subquery,
    TextField,
    Value,
)
from django.db.models.functions import Cast, Lower
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.source.snapshot_name_sql import SnapshotFamilyName
from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED
from parishkit.stewardship.web.tables import Fixed, Sorting, bounded_count, read_window

from .outbox_models import OutboxMessage
from .send_history import email_ids

FIELDS = (
    "id",
    "campaign_id",
    "family_id",
    "family__family_duid",
    "semantic_key",
    "purpose",
    "mode",
    "state",
    "version",
    "attempt",
    "task_id",
    "created_at",
    "updated_at",
    "finished_at",
)
# The purposes an Administrator resolves on an email's page: each belongs
# to a campaign, whose admission rules decide what may be done. The banner's
# count, the bulk actions and the resolution commands read these.
PURPOSES = (
    "initial",
    "reminder",
    "receipt",
    "family_test",
    "daily_digest",
    "weekly_digest",
)
# Sent mail lists every email of every kind (#1562): also the operational
# and security notices and the Ministry leader emails (#1592), which belong
# to no campaign, so their page shows their history but offers no
# resolution.
LISTED_PURPOSES = PURPOSES + ("operational", "security_event", "leader_email")
# Sent mail is a log of emails (#931): it sorts on the server by When
# (changed: the time of the email's current state, its last change; the
# default, newest first), Recipient (name), Family DUID (duid), Purpose and
# Delivery (state). The command line's --sort also takes the tokens of the
# columns Sent mail no longer shows (#1562): Mode, Provider attempts
# (attempts) and created (when the email was created). Emails to people
# (Administrators' reports and notices, Ministry leader emails), which have
# no Family, sort last under name and duid in either direction, as do,
# under name, Families the latest ParishSoft data no longer has; under
# name, the emails to people then sort by the Name or address Recipient
# shows (recipient_sort_key). Only the state filter is indexed
# (outbox_due, outbox_campaign_state); the orderings themselves are not, so
# a page is a top-N sort of the filtered messages. The default "all" view
# therefore scans the outbox, as its newest-first order always did; the
# outbox grows by about one message per Family per mailing and is not
# purged. Under name, Families that show the same name then sort by Family
# DUID, so each Family's emails stay together, as the Family codes directory
# orders tied names (directory_reports.sql). Under name and duid, one
# Family's emails then read newest change first in either direction (#934),
# as the log's default does. id is the unique tiebreak.
_FAMILY_EMAILS = (Fixed("-updated_at"), Fixed("-id"))
DELIVERY_SORTING = Sorting.by_column(
    {
        "name": (
            "family_sort_surname",
            "family_sort_name",
            "family__family_duid",
            "recipient_sort_key",
            *_FAMILY_EMAILS,
        ),
        "duid": ("family__family_duid", *_FAMILY_EMAILS),
        "purpose": ("purpose",),
        "mode": ("mode",),
        "state": ("state",),
        "attempts": ("attempt",),
        "changed": ("updated_at",),
        "created": ("created_at",),
    },
    default="-changed",
    descending_first={"attempts", "changed", "created"},
    tiebreak=("id",),
)


def with_name_keys(selected, snapshot, duid="family__family_duid"):
    """Annotate the Family name sort keys a name sort orders by.

    ``family_sort_surname`` is the surname and ``family_sort_name`` the
    whole "Squyres, Tracy and Jeff" name, both lowercased, as the Family
    codes directory orders them. Both come from ``SnapshotFamilyName``, the
    shared SQL twin of the ``snapshot_family_names`` name ``with_family_names``
    shows, with the same stand-in for a blank surname, read from
    ``snapshot``: the caller reads the current snapshot id
    once and passes it here and to ``with_family_names``, so the sort and the
    shown names agree. ``duid`` names each row's Family DUID: an outbox
    message's by default, or a refusal's ``family_duid`` on Refused
    addresses (#935). NULL (sorted last) for an Administrator report,
    which has no Family, or a Family the snapshot lacks.

    Neither an email nor a refusal stores a name, so this runs per row, and
    only when the name sort is chosen: a few index lookups and small JSON
    parses each. Computing the keys once per Family would repeat less work
    for a Family with many emails, but at a parish's few emails per Family
    the repeats are cheap.
    """
    return selected.annotate(
        **{
            field: Lower(
                SnapshotFamilyName(
                    snapshot,
                    F(duid),
                    surname_only=surname_only,
                    default=NAME_NOT_RECORDED,
                )
            )
            for field, surname_only in (
                ("family_sort_surname", True),
                ("family_sort_name", False),
            )
        }
    )


# Sent mail's grouped Delivery choices (#1562), each a set of outbox
# states. They overlap on purpose: they are filters, not a partition. Not
# sure it arrived is not "not delivered", since such an email may have
# arrived. Needs attention and Not sure it arrived — review needed also
# leave out the emails of those states that nobody can act on
# (``actionable``).
STATE_GROUPS = {
    "needs_attention": ("delivery_unknown", "permanent_failure"),
    # Not sure it arrived, but only the emails an Administrator can resolve
    # (``actionable``): the choice the status bar's and Home's "review
    # needed" counts link and count (#1576).
    "needs_review": ("delivery_unknown",),
    "not_delivered": ("permanent_failure", "cancelled"),
    "still_sending": ("pending", "retry_wait", "submitting"),
}
STATES = (
    "all",
    *STATE_GROUPS,
    "delivery_unknown",
    "permanent_failure",
    "pending",
    "retry_wait",
    "submitting",
    "delivered",
    "cancelled",
)
# How each outbox state reads to an Administrator, in the plain words the
# Family-question pages use (#523, #589). The Timeline shows the
# outcome; Sent mail and the Mail message page show the state, which
# adds where a "Still sending" email is, so a delivery problem (waiting to
# retry) stays visible. "Delivered" means the mail service accepted the
# email; the pages' About panels say that does not prove it reached the inbox.
OUTCOMES = {
    "delivered": _("Delivered"),
    "permanent_failure": _("Failed"),
    "delivery_unknown": _("Not sure it arrived"),
    "pending": _("Still sending"),
    "retry_wait": _("Still sending"),
    "submitting": _("Still sending"),
    "cancelled": _("Not sent (cancelled)"),
}
STATE_LABELS = OUTCOMES | {
    "pending": _("Still sending (queued)"),
    "retry_wait": _("Still sending (waiting to retry)"),
    "submitting": _("Still sending (handing to the mail service)"),
}


# Sent mail's grouped Purpose choices: Family email, the invitations and
# reminders System health's Waiting to retry counts and links (#1632).
PURPOSE_GROUPS = {"family": ("initial", "reminder")}
# Sent mail's Purpose filter, and its Production / Testing choice.
PURPOSE_CHOICES = ("all", *PURPOSE_GROUPS, *LISTED_PURPOSES)
MODES = ("production", "testing")
RECIPIENT_HINT = "Use a Family DUID, an email address or a delivery ID."
# An email address as Sent mail's Recipient filter takes it: one "@" with
# no spaces around it. The provider and the render guard check the rest.
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+")
# Sent mail's Recipient as the page refuses it at its field (#1562, UX
# conventions "Field errors and live checks"): the server's words, and the
# same words as the field's data-missing-hint, so Apply waits saying them.
# A name is the likeliest wrong value (the column shows names), so it says
# names are not searched.
RECIPIENT_REFUSAL = _(
    "Enter a Family DUID, an email address or a delivery ID, or clear it. "
    "Names are not searched."
)
# The Recipient field's HTML pattern: a coarse browser check of
# ``recipient_scope``, so a name holds Apply before anything is sent. It
# admits every value the server admits apart from exotic padding (a
# non-breaking space around it), and refuses any value with a letter
# outside a-f, A-F and a UUID's own "urn:" and "uuid:" prefix unless it has
# an "@" (so any name a Family has, bar a short hex-like one such as
# "Abbe", or a malformed DUID such as ",", which the server then refuses at
# the field in place). It is in
# the subset both Python's ``re`` and the browser's "v" flag read alike
# (see ``web.field_rules``).
RECIPIENT_PATTERN = (
    r"[ \t]*([0-9,]+|[^@ \t]+@[^@ \t]+|(urn:)?(uuid:)?[0-9A-Fa-f\{\}\-]+)?[ \t]*"
)


def messages():
    """Expose implemented delivery owners, never private rendered payloads."""
    return OutboxMessage.objects.filter(purpose__in=PURPOSES)


def listed_messages():
    """Every email Sent mail lists, of every kind (#1562)."""
    return OutboxMessage.objects.filter(purpose__in=LISTED_PURPOSES)


def recipient_scope(value):
    """Read Sent mail's Recipient filter: whose emails to list (#1562).

    Returns None for a blank filter, else ``(kind, value)``: ``("duid",
    int)`` for a Family DUID (with or without its thousands commas),
    ``("address", text)`` for an email address (lower-cased; the definer
    read compares in lower case) or ``("id", UUID)`` for a delivery ID
    (which also matches the Family record with that id, as before). Anything
    else raises ``ValueError``.
    """
    if type(value) is not str or len(value) > 254:
        raise ValueError(RECIPIENT_HINT)
    value = value.strip()
    if not value:
        return None
    if value.isascii() and (value.isdecimal() or "," in value):
        return ("duid", family_duid(value))
    if "@" in value:
        if _ADDRESS.fullmatch(value) is None:
            raise ValueError(RECIPIENT_HINT)
        return ("address", value.lower())
    try:
        return ("id", UUID(value))
    except ValueError:
        raise ValueError(RECIPIENT_HINT) from None


def unknown_count():
    """Count uncertainty, not failed Tasks or inferred non-acceptance."""
    return messages().filter(state="delivery_unknown").count()


def alert_counts(since, *, limit):
    """Read independent indexed warning totals in one Admin-shell round trip.

    Returns ``({event: count}, [log id], {mode: uncertain}, {event: ended})``:
    ``{mode: uncertain}`` counts, per mode, the emails Sent mail lists as Not
    sure it arrived and an Administrator can resolve (``needs_review``,
    ``by_mode``; a mode with none is missing).
    CRITICAL operational events count when they are newer than ``since`` and
    have no shared acknowledgement. The ids, oldest first and at most
    ``limit``, are the counted rows the banner's Acknowledge form may
    acknowledge; reading them in the same statement keeps them consistent
    with the counts. Matching exact rows rather than a time watermark means a
    CRITICAL row committed after an acknowledgement by a long transaction
    still appears. Scalar subqueries avoid multiplying log and outbox rows in
    a join. The immediate server-rendered warning must also work without
    browser polling.

    ``ended`` names, for each event whose problem has ended, when it ended
    (#633): every counted row was taken in by an operational incident (its
    receipt) and every such incident has resolved. A row not yet taken in,
    or an incident still open, means the problem may be going on.
    """
    # The uncertain emails are counted by Sent mail's own query (by_mode),
    # compiled into this statement, so the count is what its link lists.
    uncertain_sql, uncertain_params = by_mode("needs_review").query.sql_with_params()
    with connection.cursor() as cursor:
        cursor.execute(
            "WITH pending AS (SELECT log.id, log.event, log.created_at "
            "FROM stewardship_operational_log AS log "
            "WHERE log.level='CRITICAL' AND log.created_at>=%s AND NOT EXISTS "
            "(SELECT 1 FROM stewardship_critical_event_ack AS ack "
            "WHERE ack.log_id=log.id)), "
            "grouped AS (SELECT pending.event, count(*) AS total, "
            "bool_and(incident.resolved_at IS NOT NULL) AS ended, "
            "max(incident.resolved_at) AS ended_at FROM pending "
            "LEFT JOIN stewardship_ops_log_receipt AS receipt "
            "ON receipt.log_id=pending.id "
            "LEFT JOIN stewardship_ops_incident AS incident "
            "ON incident.id=receipt.incident_id GROUP BY pending.event) "
            "SELECT (SELECT coalesce(jsonb_object_agg(event, total), '{}'::jsonb) "
            "FROM grouped), "
            "(SELECT coalesce(array_agg(id ORDER BY created_at, id), '{}') "
            "FROM (SELECT id, created_at FROM pending "
            "ORDER BY created_at, id LIMIT %s) AS oldest), "
            "(SELECT coalesce(jsonb_object_agg(counted.mode, counted.total), "
            f"'{{}}'::jsonb) FROM ({uncertain_sql}) AS counted), "
            "(SELECT coalesce(array_agg(event ORDER BY event), '{}') "
            "FROM grouped WHERE ended), "
            "(SELECT coalesce(array_agg(ended_at ORDER BY event), '{}') "
            "FROM grouped WHERE ended)",
            (since, limit, *uncertain_params),
        )
        events, ids, unknown, ended_events, ended_times = cursor.fetchone()
    if isinstance(events, str):
        events = json.loads(events)
    if isinstance(unknown, str):
        unknown = json.loads(unknown)
    counts = {str(key): int(value) for key, value in events.items()}
    ended = dict(zip(ended_events, ended_times, strict=True))
    return counts, [UUID(str(value)) for value in ids], unknown, ended


def listing(
    window,
    *,
    state,
    query,
    sort=DELIVERY_SORTING.default,
    send=None,
    snapshot=None,
    purpose="all",
    mode=None,
    since=None,
    until=None,
    actor=None,
):
    """Sent mail's filtered page: closed choices and exact identifiers only.

    Returns (window, rows, has_next, total): ``total`` is a bounded count of
    every matching message (``web.tables.bounded_count``), and ``window`` the
    page actually read (the last one when the requested page is past it).
    ``sort`` is a DELIVERY_SORTING token the caller already validated.
    ``send``, a ``send_history.SendKey``, keeps only that Family email send's
    emails: each Family's newest one, exactly those its history counts read.
    ``snapshot`` is the ParishSoft snapshot id the Family name sort reads
    (``read_listing`` passes the current one, which the page also names its
    Families from); without one every name key is NULL.

    ``state`` is one of ``STATES`` (a state, a group or "all"); ``purpose``
    one of ``PURPOSE_CHOICES`` (a purpose, a ``PURPOSE_GROUPS`` group or
    "all"); ``mode`` "production", "testing" or None (both);
    ``since`` and ``until`` UTC bounds on When (``updated_at``), ``until``
    exclusive. ``query`` is the Recipient filter (``recipient_scope``). An
    address is resolved to its emails through the Administrator-only
    definer read for ``actor``, which raises ``PermissionError`` when it
    refuses.
    """
    scope = recipient_scope(query)
    selected = matching(
        state=state, send=send, purpose=purpose, mode=mode, since=since, until=until
    )
    if scope is not None:
        kind, value = scope
        if kind == "duid":
            selected = selected.filter(family__family_duid=value)
        elif kind == "id":
            selected = selected.filter(Q(pk=value) | Q(family_id=value))
        else:
            # Imported here: delivery_reads imports this module.
            from .delivery_reads import address_deliveries

            # The ids are found through the index on the renderings, then
            # the messages by primary key: an address has a few emails per
            # mailing, or one report a day.
            selected = selected.filter(pk__in=address_deliveries(value, actor))
    total = bounded_count(selected)
    if DELIVERY_SORTING.tokens[sort][0] == "name":
        selected = with_name_keys(selected, snapshot).annotate(
            recipient_sort_key=recipient_sort_key(selected, actor)
        )
    window, rows, has_next = read_window(
        window, DELIVERY_SORTING.order(selected, sort).values(*FIELDS), total
    )
    return window, rows, has_next, total


def matching(*, state, send=None, purpose="all", mode=None, since=None, until=None):
    """The emails Sent mail lists for these filters, before the Recipient one.

    The one definition of what a Sent mail link opens: ``listing`` narrows
    it by Recipient and reads a page of it, and every count that links to
    Sent mail (``uncertain_by_mode``, ``alert_counts``, System health's
    Waiting to retry) counts it, so a
    count and the list its link opens cannot drift apart (#1576). The
    arguments are ``listing``'s; a choice outside them raises ``ValueError``.
    """
    if (
        state not in STATES
        or purpose not in PURPOSE_CHOICES
        or mode not in (None, *MODES)
    ):
        raise ValueError("Invalid delivery filter.")
    selected = listed_messages()
    if send is not None:
        # About one id per Family, read through the definition index; the
        # messages are then found by primary key.
        selected = selected.filter(pk__in=email_ids(send))
    if state == "needs_attention":
        selected = needs_attention(selected)
    elif state == "needs_review":
        selected = actionable(selected).filter(state="delivery_unknown")
    elif state in STATE_GROUPS:
        selected = selected.filter(state__in=STATE_GROUPS[state])
    elif state != "all":
        selected = selected.filter(state=state)
    if purpose in PURPOSE_GROUPS:
        selected = selected.filter(purpose__in=PURPOSE_GROUPS[purpose])
    elif purpose != "all":
        selected = selected.filter(purpose=purpose)
    if mode is not None:
        selected = selected.filter(mode=mode)
    if since is not None:
        selected = selected.filter(updated_at__gte=since)
    if until is not None:
        selected = selected.filter(updated_at__lt=until)
    return selected


def by_mode(state):
    """``matching(state=state)`` counted per mode, as one grouped query.

    Each row is ``(mode, count)`` for a mode that has some; a mode with none
    has no row. ``alert_counts`` embeds this query's SQL, and
    ``uncertain_by_mode`` runs it, so both count exactly what Sent mail lists
    for ``?state=<state>&mode=<mode>``.
    """
    return (
        matching(state=state).order_by().values_list("mode").annotate(total=Count("pk"))
    )


def other_mode(mode):
    """The operating mode Sent mail's mode choice offers beside ``mode``."""
    return MODES[1] if mode == MODES[0] else MODES[0]


def mode_split(counts, mode):
    """Split per-mode counts into the operating mode's and the other's.

    ``counts`` maps a mode to its count (a mode with none may be missing);
    ``mode`` is the system's operating mode. Returns the dict the status
    bar, Home and System health read: ``mode`` and ``count`` (that mode's,
    the number shown), and ``other_mode`` and ``other`` (the other mode's,
    which gets its own link and is never added to ``count``, #1576).
    """
    other = other_mode(mode)
    return {
        "mode": mode,
        "count": int(counts.get(mode, 0)),
        "other_mode": other,
        "other": int(counts.get(other, 0)),
    }


def uncertain_by_mode():
    """``{mode: count}`` of the emails Sent mail lists as review needed.

    Not sure it arrived, of the kinds an Administrator can resolve
    (``needs_review``): the status bar's and Home's counts (#1576).
    """
    return dict(by_mode("needs_review"))


def actionable(selected):
    """Narrow ``selected`` to the emails whose page offers resolutions.

    Of a kind an Administrator resolves (``PURPOSES``: operational and
    security notices offer none) in a campaign that is not archived (whose
    page offers nothing). Needs attention and its Not sure it arrived part,
    needs_review, read it, so an email nobody can act on never keeps a
    "review needed" count up (#1576).
    """
    return selected.filter(purpose__in=PURPOSES).exclude(campaign__state="archived")


def needs_attention(selected):
    """Narrow ``selected`` to Sent mail's Needs attention (#1562).

    Only emails a person can still act on: Not sure it arrived, or Failed
    unless its latest decision (a resolution other than a note) was to
    record it as not sent, which settles it ("Confirm not sent", or Fix
    many's "record as not sent"). Either must be of a kind whose page offers
    resolutions (``PURPOSES``: operational and security notices offer none)
    in a campaign that is not archived (whose page offers nothing). One
    correlated lookup per candidate email, through the resolutions' history
    index; no per-row admission read.
    """
    from .delivery_resolution_models import DeliveryResolution

    decided = (
        DeliveryResolution.objects.filter(message_id=OuterRef("pk"))
        .exclude(action="note")
        .order_by("-created_at", "-id")
        .values("action")[:1]
    )
    return (
        actionable(selected)
        .annotate(last_decision=Subquery(decided))
        .filter(
            Q(state="delivery_unknown")
            | (Q(state="permanent_failure") & ~Q(last_decision="confirm_unsent"))
        )
    )


def recipient_sort_key(selected, actor):
    """The Recipient sort's key for each email to a person (#1562).

    Emails with no Family (reports and notices, to one Administrator
    address each, and Ministry leader emails, to one leader each) have no
    Family name to sort by, so under the name sort they come after every
    Family; among themselves they sort by what
    Recipient shows (``delivery_reads.person_names``: the Users-page Name,
    else a ParishSoft Member's name, else the address), case-folded.
    Those keys come from the definer read of their addresses
    (``delivery_reads.administrator_addresses``), so they are read in
    Python for every such email the filters match, and the database then
    looks each one up by id in a JSON object: one id query, one definer
    read per 500 such emails and one query of the users (and one of the
    Member names when an address has no Users-page Name). Family emails get
    NULL. Without an actor (no definer read is possible) every key is NULL,
    so they keep newest first, as before.
    """
    from .delivery_reads import administrator_addresses, person_names

    keys = {}
    if actor is not None:
        ids = list(selected.filter(family__isnull=True).values_list("pk", flat=True))
        addresses = administrator_addresses(ids, actor)
        names = person_names(addresses.values())
        keys = {
            str(message): (names.get(address.lower()) or address).casefold()
            for message, address in addresses.items()
        }
    return Func(
        Cast(Value(json.dumps(keys)), JSONField()),
        Cast(F("pk"), TextField()),
        template="(%(expressions)s)",
        arg_joiner=" ->> ",
        output_field=TextField(),
    )


# A typed Family DUID: its digits, or the digits grouped by commas as a
# copied US-formatted number shows them, at most DUID_LONGEST characters.
# The Test with Families page checks the same rule in the browser (#1577).
DUID_TEXT = r"[0-9]+|[1-9][0-9]{0,2}(?:,[0-9]{3})+"
DUID_LONGEST = 25
NOT_A_DUID = "Use an exact Family DUID."


def family_duid(value):
    """Accept copied US-grouped identifiers as well as their canonical digits."""
    if type(value) is not str or len(value) > DUID_LONGEST:
        raise ValueError(NOT_A_DUID)
    if re.fullmatch(DUID_TEXT, value) is None:
        raise ValueError(NOT_A_DUID)
    result = int(value.replace(",", ""))
    if not 0 < result < 2**63:
        raise ValueError(NOT_A_DUID)
    return result
