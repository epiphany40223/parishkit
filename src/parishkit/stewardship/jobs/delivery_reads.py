"""Sent mail and refused address reads, moved out of the delivery views.

The Admin pages (``jobs.delivery_views``) and the Admin automation command
line (``delivery list``, ``delivery show``, ``delivery refusals`` and
``delivery refusal-show``, ADM-11 PR 9b) read through these functions, so
both see the same rows, filters, sort orders and offered actions. Nothing
here takes a request or checks authority: each caller admits first, reads
inside its own transaction and rechecks afterwards. ``parameters`` is the
page's query (a ``QueryDict``); repeated, unknown or malformed options are
refused (``ValueError``) as on the page.
"""

import re
from datetime import timedelta
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters
from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .delivery_metadata import (
    DELIVERY_SORTING,
    FIELDS,
    MODES,
    PURPOSES,
    family_duid,
    listed_messages,
    listing,
    messages,
    with_name_keys,
)

# Every Refused addresses column but Email sorts on the server; the default
# keeps the old Family DUID, then address, order. Family (name) sorts as
# Sent mail's does: surname, then the whole shown name, then Family DUID
# (so each Family's addresses stay together) and address, read from the latest
# ParishSoft data (delivery_metadata.with_name_keys), with Families that
# data no longer has last in both directions. id has no column (the
# refusal's own page shows it under Technical details) but keeps its sort
# for the command line's --sort id, and is the unique tiebreak. No index
# orders these keys (the recipient_refusal_identity index leads with
# organization_id, then family_duid), so each is a top-N sort over the
# unresolved refusals, a small set (one row per refused Family address).
REFUSAL_SORTING = Sorting.by_column(
    {
        "address": ("address",),
        "name": ("family_sort_surname", "family_sort_name", "family_duid", "address"),
        "duid": ("family_duid", "address"),
        "refused": ("created_at",),
        "id": ("id",),
    },
    default="duid",
    descending_first={"refused"},
    tiebreak=("id",),
)


def parse_window(parameters, allowed, sorting=None):
    """Bound all lists and reject repeated, unknown or malformed query options.

    A list passes its ``sorting``, which accepts and validates ``sort``.
    Returns ``(values, window)``.
    """
    names = {"page", "size", *allowed, *({"sort"} if sorting else ())}
    values = filters(parameters, allowed=names)
    if sorting is not None:
        values["sort"] = sorting.parse(values)
    return values, PageWindow(
        expected_version(values.get("page", "1")),
        expected_version(values.get("size", "25")),
    )


# Sent mail's filters (#1562), as query parameters: the Recipient text,
# the Purpose and Delivery choices, the From and Through days with the
# browser zone they are in, the mode and a Family email send.
LISTING_FILTERS = {
    "state",
    "recipient",
    "purpose",
    "start",
    "end",
    "zone",
    "mode",
    "send",
}


def day_bounds(values):
    """Sent mail's From and Through days as UTC bounds on When (#1562).

    Returns ``(since, until)``: the instant ``start`` begins and the instant
    the day after ``end`` begins, in the browser zone ``zone`` names, or
    None for a blank day. A malformed day, a Through before From, or a day
    without a known zone raises ``ValueError`` (``UnknownZone`` for the
    zone), so a filter is never read in another zone than the reader's.
    """
    from datetime import date

    from parishkit.stewardship.web.dates import browser_day_start

    days = []
    for name in ("start", "end"):
        text = values.get(name, "")
        try:
            # Only the date control's own spelling, as System logs reads it.
            if text and not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text):
                raise ValueError
            days.append(date.fromisoformat(text) if text else None)
        except ValueError:
            raise ValueError("Use whole dates for From and Through.") from None
    start, end = days
    if start and end and end < start:
        raise ValueError("Through must not be before From.")
    zone = values.get("zone", "")
    return (
        browser_day_start(start, zone) if start else None,
        browser_day_start(end + timedelta(days=1), zone) if end else None,
    )


def read_listing(parameters, *, actor=None, default_mode=None):
    """Sent mail: one filtered page of messages, never their content.

    Returns ``values`` (the validated filters), ``window``, ``rows``,
    ``has_next``, ``total`` (a ``bounded_count``), ``send`` (the described
    Family email send it is limited to, or None) and ``snapshot``: the
    current ParishSoft snapshot id the Family sort read, which the page
    passes to ``with_family_names`` so the shown names match the order (or
    None before the first ParishSoft sync). A malformed or
    unknown send is refused rather than ignored, so a filtered link never
    silently lists every email instead (#432).

    ``actor`` is the admitted Administrator, whom the address filter's
    definer read names. ``mode`` defaults to the named send's mode, else to
    ``default_mode``: Production on the page; both modes (None) on the
    command line, as before (#1562).
    """
    from .send_history import SendKey, describe

    values, window = parse_window(parameters, LISTING_FILTERS, DELIVERY_SORTING)
    state, query = values.get("state", "all"), values.get("recipient", "")
    purpose = values.get("purpose", "all")
    send = described = None
    if "send" in values:
        send = SendKey.parse(values["send"])
        described = describe(send)
        if described is None:
            raise ValueError("Unknown Family email send.")
    mode = values.get("mode") or (send.mode if send else default_mode)
    if mode not in (None, *MODES):
        raise ValueError("Invalid delivery filter.")
    since, until = day_bounds(values)
    snapshot = current_snapshot()
    window, rows, has_next, total = listing(
        window,
        state=state,
        query=query,
        sort=values["sort"],
        send=send,
        snapshot=snapshot,
        purpose=purpose,
        mode=mode,
        since=since,
        until=until,
        actor=actor,
    )
    return dict(
        values=values
        | {"state": state, "recipient": query, "purpose": purpose, "mode": mode},
        window=window,
        rows=rows,
        has_next=has_next,
        total=total,
        send=described,
        snapshot=snapshot,
    )


def current_snapshot():
    """The current ParishSoft snapshot id, or None before the first sync."""
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    return SourceCurrent.objects.values_list("snapshot_id", flat=True).first()


def with_family_names(rows, snapshot, key="family__family_duid"):
    """Each listed row with its Family's name, for the Admin mail pages (#931).

    ``family_name`` is the directory's surname-and-heads name from
    ``snapshot`` (the one the list was sorted by), read by
    ``snapshot_family_names`` as the Timeline names a Family ("Name
    not recorded in ParishSoft" for a blank surname, #1216), or None for an
    Administrator report and for a Family that snapshot lacks.
    The email itself stores no name, so the names are read for the shown
    page only: at most two small queries (its Families and their heads),
    however many rows. The command line's ``delivery list`` does not read
    them. ``key`` names each row's Family DUID: an email's
    ``family__family_duid`` on Sent mail, a refusal's ``family_duid`` on
    Refused addresses (#935).
    """
    from parishkit.stewardship.source.snapshot_names import snapshot_family_names
    from parishkit.stewardship.source.source_status import NAME_NOT_RECORDED

    duids = {row[key] for row in rows} - {None}
    names = snapshot_family_names(snapshot, duids, NAME_NOT_RECORDED)
    return [row | {"family_name": names.get(row[key])} for row in rows]


def unresolved_refusals():
    """Every refused address not yet cleared or superseded (#935).

    A refusal is resolved by exactly one resolution row (a verified
    clearance, or ParishSoft's address changing), so the unresolved ones are
    those without one. The set stays small: one row per permanently refused
    Family address.
    """
    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    return RecipientRefusal.objects.exclude(
        pk__in=RecipientRefusalResolution.objects.values("refusal_id")
    )


def recording_message():
    """The email whose delivery outcome recorded a refusal, as a subquery.

    A refusal keeps only its outbox event (``event_id``); the event names its
    message. The web login reads both columns, never the event's evidence.
    """
    from django.db.models import OuterRef, Subquery

    from .outbox_models import OutboxEvent

    return Subquery(
        OutboxEvent.objects.filter(pk=OuterRef("event_id")).values("message_id")[:1]
    )


def with_refusal_counts(rows):
    """Each listed email with how many of its refused addresses are unresolved.

    Sent mail marks such an email ("1 address refused", #935): a refused
    address does not fail an email another address accepted, so without the
    mark a Delivered email would hide that one head of household never got
    it. Each address counts once: a retry that is refused again records a
    second refusal of the same address against the same email. One grouped
    query for the shown page, however many rows; an email with none counts 0.
    """
    from django.db.models import Count

    from .outbox_models import OutboxEvent

    ids = [row["id"] for row in rows]
    counts = dict(
        unresolved_refusals()
        .filter(
            event_id__in=OutboxEvent.objects.filter(message_id__in=ids).values("id")
        )
        .annotate(message=recording_message())
        .values("message")
        .annotate(count=Count("address", distinct=True))
        .values_list("message", "count")
    )
    return [row | {"refused": counts.get(row["id"], 0)} for row in rows]


def email_refusals(message_id):
    """The unresolved refused addresses one email's delivery recorded (#1015).

    The Mail message page names them, each linking its refusal's own page,
    since the web login never reads the email's recipient list (its
    rendering) and the refused addresses are the ones an Administrator acts
    on. Each address is listed once, by its newest refusal: a retry refused
    again records a second refusal of the same address, as
    ``with_refusal_counts`` counts them. Returns ``[{"id", "address",
    "kind"}]`` in address order, ``kind`` saying whether the provider refused
    it or an Administrator reported it bounced (#806 slice 4); one query.
    """
    from .outbox_models import OutboxEvent

    newest = {}
    for row in (
        unresolved_refusals()
        .filter(
            event_id__in=OutboxEvent.objects.filter(message_id=message_id).values("id")
        )
        .order_by("address", "-created_at", "-id")
        .values("id", "address", "kind")
    ):
        newest.setdefault(row["address"], row)
    return list(newest.values())


# What an address's latest attempt recorded (#806), as the Mail message page
# says it after the address; the keys are the outcomes
# stewardship_delivery_recipient_outcomes_v1 (frozen migration 0068) returns.
OUTCOME_LABELS = {
    "delivered": _("delivered"),
    "refused_for_now": _("refused for now"),
    "refused_permanently": _("refused permanently"),
    "unknown": _("not sure it arrived"),
    "not_delivered": _("not delivered"),
    # From the email's follow-up to its addresses refused for now (#806
    # slice 2, stewardship_delivery_address_retries_v1, frozen migration
    # 0077): still being tried, or a try whose outcome is unknown.
    "retrying": _("refused for now, trying again"),
    "retry_unknown": _("not sure it arrived"),
}
# The email states after which nothing more is sent for it.
FINAL_STATES = frozenset({"delivered", "permanent_failure", "cancelled"})


def outcome_label(outcome, state):
    """The label to show after an address, given the email's current state.

    The outcome is what the latest attempt's evidence recorded; ``state`` is
    the email's state now, which an Administrator's resolution or a retry
    may have moved on since. "Not sure it arrived" is shown only while the
    email is still in that state: once an Administrator confirms delivery or
    records that it was not sent, or a resend is authorized, the provider's
    uncertainty no longer describes the email, so the address shows nothing.
    "Not delivered" reads "not delivered yet" while the email may still be
    sent (it waits for a retry or is being sent again). Returns None when
    nothing should be shown.
    """
    if outcome == "unknown" and state != "delivery_unknown":
        return None
    if outcome == "not_delivered" and state not in FINAL_STATES:
        return _("not delivered yet")
    return OUTCOME_LABELS[outcome]


def _definer_rows(sql, parameters):
    """Run one Administrator-only definer read; its rows, or None if refused.

    When the function refuses (SQLSTATE 42501), for example because the
    actor stopped being an Administrator after the page admitted them, this
    returns None and the page renders without what it read: the page's
    final authority recheck then refuses the whole answer, rather than the
    read failing as a database error. The call runs in its own savepoint so
    the refusal does not abort the page's transaction. Any other database
    error is raised.
    """
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()
    except DatabaseError as error:
        if getattr(error.__cause__, "sqlstate", None) != "42501":
            raise
        return None


def address_deliveries(address, actor_id):
    """The ids of the emails meant for ``address``, for Sent mail (#1562).

    Read through ``stewardship_delivery_ids_for_address_v1`` (frozen
    migration 0116), which compares in lower case over the renderings'
    intended recipients, through their index, and returns nothing else.
    Raises ``PermissionError`` when it refuses ``actor_id`` (see
    ``_definer_rows``): the page then answers with its denial.
    """
    rows = _definer_rows(
        "SELECT * FROM public.stewardship_delivery_ids_for_address_v1(%s,%s)",
        (address, actor_id),
    )
    if rows is None:
        raise PermissionError("Email recipients require an Administrator.")
    return [row[0] for row in rows]


def administrator_names(addresses):
    """The Users-page Name of each address, lower-cased, that has one (#1562).

    Reads the current configuration's users (``AddressRule``) in one query.
    An address with no entry there (a former Administrator), or whose entry
    has no Name, is left out, so the page shows the address itself.
    """
    from django.db.models import Subquery
    from django.db.models.functions import Lower

    from parishkit.stewardship.accounts.policy_models import AddressRule
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    wanted = {address.lower() for address in addresses}
    if not wanted:
        return {}
    active = SystemConfiguration.objects.values("active_configuration_id")[:1]
    return dict(
        AddressRule.objects.filter(configuration_id=Subquery(active))
        .annotate(lowered=Lower("email"))
        .filter(lowered__in=wanted, name__gt="")
        .values_list("lowered", "name")
    )


# The most ids one call of stewardship_delivery_page_recipients_v1 takes.
PAGE_RECIPIENTS_LIMIT = 500


def administrator_addresses(ids, actor_id):
    """``{message id: address}`` for emails with no Family (#1562).

    Read through ``stewardship_delivery_page_recipients_v1`` (frozen
    migration 0116), one call per ``PAGE_RECIPIENTS_LIMIT`` ids: a page
    makes one, the Recipient sort one per 500 such emails. A report or
    notice goes to one address; the first is kept. Raises
    ``PermissionError`` when the read refuses ``actor_id``.
    """
    ids = list(ids)
    addresses = {}
    for start in range(0, len(ids), PAGE_RECIPIENTS_LIMIT):
        found = _definer_rows(
            "SELECT message_id, address FROM "
            "public.stewardship_delivery_page_recipients_v1(%s::uuid[],%s)",
            (ids[start : start + PAGE_RECIPIENTS_LIMIT], actor_id),
        )
        if found is None:
            raise PermissionError("Email recipients require an Administrator.")
        for message_id, address in found:
            addresses.setdefault(message_id, address)
    return addresses


def with_recipients(rows, actor_id):
    """Each listed email with whom it is for, for Sent mail's Recipient (#1562).

    A Family email is named by its Family (``with_family_names``). An email
    with no Family (a report or notice, sent to one Administrator address)
    gains ``recipient_address``, read for the shown page through
    ``stewardship_delivery_page_recipients_v1`` (frozen migration 0116),
    and ``recipient_name``, that address's Users-page Name or None: one
    definer read and one query, however many rows, and none when the page
    lists only Family emails. Raises ``PermissionError`` when the read
    refuses ``actor_id``.
    """
    addresses = administrator_addresses(
        [row["id"] for row in rows if row["family_id"] is None], actor_id
    )
    names = administrator_names(addresses.values())
    return [
        row
        | {
            "recipient_address": addresses.get(row["id"]),
            "recipient_name": names.get((addresses.get(row["id"]) or "").lower()),
        }
        for row in rows
    ]


def email_recipients(message_id, actor_id, state):
    """One email's recipient addresses, for its Mail message page (#1024).

    The addresses live only in the email's rendering, which the web login
    may not read; ``stewardship_delivery_recipients_v1`` (frozen migration
    0058) returns them, and nothing else from the rendering, only when
    ``actor_id`` is an Administrator. Returns ``{"intended": [...],
    "routed": [...]}``, each list in its stored order: whom the email is
    for, and where it was sent (the same addresses in Production; the
    Testing recipient in Testing mode). Both lists are empty for an email
    with no rendering yet.

    Each list also comes as ``intended_rows`` and ``routed_rows``: pairs of
    an address and the label of what the latest attempt recorded for it,
    given the email's current ``state`` (#806, see ``address_outcomes`` and
    ``outcome_label``), or None where there is none to show.
    ``reached`` is ``(delivered, total)`` when an accepted attempt reached
    only some of its addresses, ``refused_for_now`` says whether any
    address was refused for now and not tried again, and ``retrying``
    whether any is still being tried again (#806 slice 2).

    Returns None when either definer read refuses (see ``_definer_rows``).
    """
    rows = _definer_rows(
        "SELECT kind, address FROM public.stewardship_delivery_recipients_v1(%s,%s)",
        (message_id, actor_id),
    )
    outcomes = None if rows is None else address_outcomes(message_id, actor_id)
    if outcomes is None:
        return None
    # An address refused for now may have been followed up since (#806):
    # what the follow-up did replaces the attempt's "refused for now".
    if any(pair[1] == "refused_for_now" for pair in outcomes):
        follow_ups = address_follow_ups(message_id, actor_id)
        if follow_ups is None:
            return None
        outcomes = followed_up(outcomes, follow_ups)
    recipients = {"intended": [], "routed": []}
    for kind, address in rows:
        recipients[kind].append(address)
    # The outcomes describe the routed envelope. Use them only when they
    # are for exactly the list shown: the two reads are separate statements,
    # so a rendering made between them must not mislabel an address.
    labels = dict(outcomes)
    if [pair[0] for pair in outcomes] != recipients["routed"]:
        labels = {}
    for kind in ("intended", "routed"):
        # In Production the routed list is the intended one, so the To: line
        # carries the outcomes; in Testing mode they follow the Testing line.
        shown = kind == "routed" or recipients["routed"] == recipients["intended"]
        recipients[kind + "_rows"] = [
            (
                address,
                outcome_label(labels[address], state) if shown and labels else None,
            )
            for address in recipients[kind]
        ]
    # Counted over the envelope, so a repeated address counts each time.
    found = [pair[1] for pair in outcomes] if labels else []
    delivered = found.count("delivered")
    recipients["reached"] = (
        (delivered, len(found)) if 0 < delivered < len(found) else None
    )
    recipients["refused_for_now"] = "refused_for_now" in found
    recipients["retrying"] = "retrying" in found
    return recipients


def followed_up(outcomes, follow_ups):
    """``outcomes`` with each followed-up position's outcome replaced.

    ``follow_ups`` are ``(position, outcome)`` pairs indexing the same
    routed envelope; a position outside it is ignored.
    """
    merged = list(outcomes)
    for position, outcome in follow_ups:
        if 0 <= position < len(merged):
            merged[position] = (merged[position][0], outcome)
    return merged


def address_follow_ups(message_id, actor_id):
    """What the email's follow-up did for each address refused for now.

    Read through ``stewardship_delivery_address_retries_v1`` (frozen
    migration 0077, #806), with the same caller checks as the outcomes read,
    for the same attempt: ``(position, outcome)`` pairs, the outcome being
    "delivered", "refused_permanently", "retrying", "retry_unknown" or
    "refused_for_now" (given up). None of them when the attempt opened no
    follow-up. Returns None when refused (see ``_definer_rows``).
    """
    rows = _definer_rows(
        "SELECT envelope_position, outcome"
        " FROM public.stewardship_delivery_address_retries_v1(%s,%s)",
        (message_id, actor_id),
    )
    return None if rows is None else [tuple(row) for row in rows]


def address_outcomes(message_id, actor_id):
    """What the latest attempt recorded for each of an email's routed addresses.

    Read through ``stewardship_delivery_recipient_outcomes_v1`` (frozen
    migration 0068, #806), which validates the attempt's stored evidence
    and returns ``(address, outcome)`` pairs in envelope order, outcome
    being a key of ``OUTCOME_LABELS``; it returns none before the newest
    rendering's first finished attempt. Returns None when refused (see
    ``_definer_rows``).
    """
    rows = _definer_rows(
        "SELECT address, outcome"
        " FROM public.stewardship_delivery_recipient_outcomes_v1(%s,%s)",
        (message_id, actor_id),
    )
    return None if rows is None else [tuple(row) for row in rows]


# Why the Mail message page's "Report a bounced address" is unavailable
# (#806 slice 4), by the first missing prerequisite.
BOUNCE_UNAVAILABLE = {
    "not_delivered": _(
        "Available once this email is delivered: only a delivered email can bounce."
    ),
    "closed": _(
        "Not available right now. It needs a current campaign that accepts "
        "changes and up-to-date ParishSoft data."
    ),
    "none_left": _("Every address of this email is already set aside."),
}


def current_recipients(family_id):
    """The Family's current, unrefused addresses, as preparation reads them.

    An invoker read of ParishSoft data web already reads; a reported bounce
    of the only one leaves the Family with no campaign email (#806).
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT jsonb_array_elements_text("
            "public.stewardship_family_mail_recipients_v1(%s))",
            [family_id],
        )
        return [address for (address,) in cursor.fetchall()]


def only_address_left(message_id, address):
    """Whether ``address`` is the only one left of the email's Family.

    The panel's ``last`` notice for ``delivery bounce-report``'s prompt
    (#1211): reporting it would leave the Family with no campaign email.
    False for an unknown email or one with no Family; the report itself
    then refuses it.
    """
    from .outbox_models import OutboxMessage

    family_id = (
        OutboxMessage.objects.filter(pk=message_id)
        .values_list("family_id", flat=True)
        .first()
    )
    return family_id is not None and current_recipients(family_id) == [address]


def bounce_panel(delivery, recipients, *, can_change):
    """What the Mail message page's "Report a bounced address" panel shows.

    Only a Production Family email has the panel (#806 slice 4); returns None
    for Testing mail and Administrator reports. Otherwise returns
    ``addresses``, the email's addresses an Administrator may report (those
    the Family has no unresolved refusal of, and none recorded against the
    email's newest delivered attempt, which a report would name),
    ``unavailable``, the reason the report cannot be made now (a
    ``BOUNCE_UNAVAILABLE`` text) or None, and ``reported``, the bounce
    reports already recorded for this email, oldest first, each with
    ``cleared`` when it has been cleared. ``last`` is the Family's only
    address left, when it is one of the offered ones: its current recipients
    (the ParishSoft addresses with no unresolved refusal, as the next email
    would read them) are that one address, so reporting it would leave the
    Family with no campaign email; otherwise None. ``recipients`` is
    ``email_recipients``' answer (None when its definer refused, which
    leaves no address to offer); ``can_change`` says the campaign accepts
    Admin changes (the page offers its evidence note).
    """
    from django.db.models import Exists, OuterRef

    from .outbox_models import OutboxEvent
    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    if delivery["mode"] != "production" or delivery["family__family_duid"] is None:
        return None
    events = OutboxEvent.objects.filter(message_id=delivery["id"])
    reported = list(
        RecipientRefusal.objects.filter(
            kind="reported_bounce", event_id__in=events.values("id")
        )
        .annotate(
            cleared=Exists(
                RecipientRefusalResolution.objects.filter(refusal_id=OuterRef("pk"))
            )
        )
        .order_by("created_at", "id")
        .values("id", "address", "created_at", "cleared")
    )
    delivered = (
        events.filter(state="delivered")
        .order_by("-version")
        .values_list("id", flat=True)
        .first()
    )
    taken = set(
        unresolved_refusals()
        .filter(family_duid=delivery["family__family_duid"])
        .values_list("address", flat=True)
    ) | set(
        RecipientRefusal.objects.filter(event_id=delivered).values_list(
            "address", flat=True
        )
    )
    shown = (recipients or {}).get("intended", [])
    addresses = [address for address in dict.fromkeys(shown) if address not in taken]
    current = current_recipients(delivery["family_id"])
    last = current[0] if len(current) == 1 and current[0] in addresses else None
    if delivery["state"] != "delivered" or delivered is None:
        reason = "not_delivered"
    elif not can_change:
        reason = "closed"
    elif not addresses:
        reason = "none_left"
    else:
        reason = None
    return dict(
        addresses=addresses,
        unavailable=BOUNCE_UNAVAILABLE[reason] if reason else None,
        reported=reported,
        last=last,
        command_id=uuid4(),
    )


def offered_actions(state, task_state, *, campaign_state, can_resolve, can_retry):
    """The resolutions a message's page offers now, in the page's order.

    ``state`` is the message's state and ``task_state`` its latest delivery
    task's (None without one); ``can_resolve`` is the campaign's export
    admission and ``can_retry`` the message's resend admission
    (``stewardship_delivery_retry_admitted_v1``). The bulk actions on
    Sent mail (``delivery_bulk``) select by these same rules, so a bulk
    action never touches a message whose own page would not offer it.
    """
    actions = ["note"] if campaign_state != "archived" and can_resolve else []
    if task_state == "failed" and actions:
        if state == "delivery_unknown":
            # Both settle the attempt from external evidence without a
            # send, so neither depends on the resend admission (can_retry).
            actions += ["accept", "confirm_unsent"]
            if can_retry:
                actions.append("resend")
        elif can_retry:
            if state == "permanent_failure":
                actions.append("retry_failed")
            elif state in {"pending", "retry_wait"}:
                actions.append("retry_unsent")
    return actions


# The question each state's outcome panel asks (#1563), as (choice,
# action) pairs in the page's order: each answer is exactly one existing
# resolution action. Not sure it arrived asks "What did you find?" ("It
# was sent", "It was not sent" then "Send it again?" Yes or No, "I can't
# tell yet"); Failed asks "Send it again?"; a still-sending email whose
# background task failed offers only "Try again now".
QUESTIONS = {
    "delivery_unknown": (
        ("sent", "accept"),
        ("resend", "resend"),
        ("not_sent", "confirm_unsent"),
        ("unsure", "note"),
    ),
    "permanent_failure": (("send_again", "retry_failed"), ("keep_failed", "note")),
    "pending": (("try_again", "retry_unsent"),),
    "retry_wait": (("try_again", "retry_unsent"),),
}
# The actions that send the email again, whose refusal the page explains by
# the admission condition that failed (retry_refusal).
SENDING_ACTIONS = frozenset({"resend", "retry_failed", "retry_unsent"})
# Why an answer is unavailable, in the order the codes are checked (#1563;
# admin-portal spec, Resolution questions). The last five explain a refused
# send only.
UNAVAILABLE_REASONS = {
    "no_campaign": _("Notices belong to no campaign, so they can't be resolved here."),
    "archived": _("The campaign is archived, so its email can't be changed."),
    "held": _(
        "Other campaign work (a restore review, or a cleanup, purge or go-live "
        "step) is under way, so this email can't be resolved until it "
        "finishes or is cancelled."
    ),
    "task_running": _(
        "This email's background task has not finished, so it can be "
        "answered once it has."
    ),
    "campaign_ended": _("The campaign has ended, so it is not sent again."),
    "family_responded": _("The Family has already responded, so it is not sent again."),
    "family_unreachable": _(
        "The Family can no longer be emailed (ParishSoft no longer gives it a "
        "usable address, or it left the campaign)."
    ),
    "paused": _("Campaign email is paused; it can be sent again once mail is resumed."),
    "not_admitted": _(
        "Sending it again is not available right now. It needs the campaign "
        "to be open, campaign email not paused, up-to-date ParishSoft data, "
        "and no other email already covering this one."
    ),
}


def email_outcome(state, refused):
    """Which outcome panel one email's page shows (#1563).

    ``refused`` says the email's delivery recorded unresolved refused
    addresses: a delivered email with one still needs the Administrator,
    who checks and clears it.
    """
    if state == "delivered":
        return "needs_resolution" if refused else "delivered"
    if state == "cancelled":
        return "cancelled"
    if state in {"delivery_unknown", "permanent_failure"}:
        return "needs_resolution"
    return "still_sending"


def question_choices(state, task_state, actions, *, blocked, refusal):
    """The answers an email's outcome panel offers, with why any is unavailable.

    Returns ``[{"choice", "action", "unavailable"}]`` in the page's order
    (``QUESTIONS``); ``unavailable`` is None for an answer whose action is
    offered now (it is in ``actions``, from ``offered_actions``, so the
    page never offers what the server would refuse) or else a key of
    ``UNAVAILABLE_REASONS``. ``blocked`` is the reason nothing can be
    changed (no campaign, archived, held) or None; ``refusal`` is why the
    resend admission refused a send (``retry_refusal``), or None. A
    still-sending email asks nothing until its background task has failed,
    as before.
    """
    if state in {"pending", "retry_wait"} and task_state != "failed":
        return []
    rows = []
    for choice, action in QUESTIONS.get(state, ()):
        if action in actions:
            reason = None
        elif blocked:
            reason = blocked
        elif task_state != "failed":
            reason = "task_running"
        else:
            # Every other answer is offered whenever the campaign accepts
            # changes and the task failed, so only a send can be refused here.
            reason = refusal or "not_admitted"
        rows.append(dict(choice=choice, action=action, unavailable=reason))
    return rows


def retry_refusal(message):
    """Which condition refuses sending ``message`` again, for its page (#1563).

    Called only after ``stewardship_delivery_retry_admitted_v1`` refused,
    so the admitted or refused answer is always the server's own; this only
    names a likely cause, and returns None (the generic reason) when none of
    these explains it. One query of columns the web login reads, in the
    order the page names them: a Production invitation or reminder whose
    campaign is no longer scheduled or active or whose end has passed
    (``campaign_ended``), or whose Family has responded
    (``family_responded``); a Production Family email whose Family is no
    longer active, eligible and deliverable (``family_unreachable``); or
    Production campaign email paused, which fences every send but the
    resend of a delivery that may have arrived (``paused``). Testing mail
    is never diagnosed: its admission follows the rehearsal instead.
    A campaign with no active configuration reads as not ended: the
    answer then names the generic reason rather than "campaign ended".
    """
    if message["mode"] != "production":
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.state IN ('scheduled','active')"
            " AND COALESCE(p.ends_at>now_at, true),"
            " f.effective_submission_id IS NOT NULL,"
            " f.active AND f.email_eligible AND f.email_deliverable,"
            " c.delivery_paused"
            " FROM stewardship_campaign c"
            " CROSS JOIN LATERAL (SELECT stewardship_campaign_now_v1() AS now_at) n"
            " LEFT JOIN stewardship_campaign_configuration p"
            " ON p.id=c.active_configuration_id"
            " LEFT JOIN stewardship_family_campaign f ON f.id=%s"
            " WHERE c.id=%s",
            (message["family_id"], message["campaign_id"]),
        )
        found = cursor.fetchone()
    if found is None:
        return None
    open_now, responded, reachable, paused = found
    invitation = message["purpose"] in {"initial", "reminder"}
    for reason, holds in (
        ("campaign_ended", invitation and not open_now),
        ("family_responded", invitation and responded),
        ("family_unreachable", message["family_id"] is not None and not reachable),
        ("paused", paused and message["state"] != "delivery_unknown"),
    ):
        if holds:
            return reason
    return None


def read_detail(message_id, parameters):
    """One delivery: its metadata, history, latest task, notes and actions.

    The history's upper version is pinned to the message read, so a page
    and its history agree. ``actions`` are the resolutions the page offers
    now, in its order; ``retry_unavailable`` says a retry would be offered
    but the campaign's resend admission refuses it. ``refused`` are the
    email's unresolved refused addresses (``email_refusals``).

    The outcome panel (#1563): ``outcome`` (``email_outcome``) and
    ``choices``, its question's answers with why any is unavailable
    (``question_choices``): nothing can be changed for a notice
    (``no_campaign``), on an archived campaign, or while other campaign
    work holds it (``held``, the export admission refusing, #1331); a
    refused send is named by ``retry_refusal``. ``paused`` says campaign
    email is paused and ``last_attempt_at`` is when the provider was last
    asked to send it (None before any attempt). Raises
    ``OutboxMessage.DoesNotExist`` for an unknown message.

    Every email Sent mail lists opens here, operational and security
    notices too (#1562). Those belong to no campaign, and every resolution
    and its admission is a campaign's, so they offer nothing and their
    admission is not read.
    """
    from parishkit.stewardship.campaigns.models import Campaign

    from .delivery_resolution_models import DeliveryResolution
    from .models import TaskRun
    from .outbox_models import OutboxEvent

    window = parse_window(parameters, set())[1]
    message = listed_messages().values(*FIELDS, "not_before").get(pk=message_id)
    events, events_following = window.rows(
        OutboxEvent.objects.filter(
            message_id=message_id, version__lte=message["version"]
        )
        .order_by("-version")
        .values("created_at", "version", "state", "action", "attempt", "reason")
    )
    task = (
        TaskRun.objects.filter(root_id=message["task_id"])
        .order_by("-retry_sequence")
        .values("id", "state", "version", "retry_sequence")
        .first()
    )
    resolvable = message["purpose"] in PURPOSES and message["campaign_id"]
    campaign_state, can_resolve, can_retry = "archived", False, False
    paused = False
    if resolvable:
        campaign_state, paused = (
            Campaign.objects.filter(pk=message["campaign_id"])
            .values_list("state", "delivery_paused")
            .get()
        )
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT stewardship_export_admitted_v1(%s,true), "
                "stewardship_delivery_retry_admitted_v1(%s)",
                (message["campaign_id"], message_id),
            )
            can_resolve, can_retry = cursor.fetchone()
    actions = offered_actions(
        message["state"],
        task and task["state"],
        campaign_state=campaign_state,
        can_resolve=can_resolve,
        can_retry=can_retry,
    )
    notes, notes_following = window.rows(
        DeliveryResolution.objects.filter(message_id=message_id)
        .order_by("-created_at", "-id")
        .values("created_at", "action", "evidence_note")
    )
    refused = email_refusals(message_id)
    task_state = task and task["state"]
    retry_unavailable = bool(
        resolvable
        and task
        and task["state"] == "failed"
        and not can_retry
        and message["state"]
        in {"delivery_unknown", "permanent_failure", "pending", "retry_wait"}
    )
    blocked = (
        "no_campaign"
        if not resolvable
        else "archived"
        if campaign_state == "archived"
        else "held"
        if not can_resolve
        else None
    )
    # The cause is looked up only for a send the admission refused, and
    # only on a page that would otherwise offer it.
    refusal = retry_refusal(message) if retry_unavailable and blocked is None else None
    return dict(
        delivery=message,
        events=events,
        task=task,
        notes=notes,
        window=window,
        has_next=events_following or notes_following,
        actions=actions,
        retry_unavailable=retry_unavailable,
        refused=refused,
        # The outcome panel (#1563): which one, its answers, whether
        # campaign email is paused (a still-sending email waits for
        # resume) and when the provider was last asked to send it.
        outcome=email_outcome(message["state"], bool(refused)),
        choices=question_choices(
            message["state"],
            task_state,
            actions,
            blocked=blocked,
            refusal=refusal,
        ),
        paused=paused,
        last_attempt_at=OutboxEvent.objects.filter(
            message_id=message_id, action="submit", version__lte=message["version"]
        )
        .order_by("-version")
        .values_list("created_at", flat=True)
        .first(),
    )


def _long_retry():
    """The wait past which a retry is on a limit or throttle backoff, not the usual.

    The ordinary retry schedule (``family_mail_dispatch.retry_delay``) waits
    at most 10 minutes; the recipient-throttle backoff and a Gmail limit
    refusal wait at least 15. Halfway between keeps a few seconds of clock
    difference between the message's ``updated_at`` and ``not_before`` from
    mattering.
    """
    from .family_mail_dispatch import (
        LIMIT_RETRY_SECONDS,
        MAX_ATTEMPTS,
        RECIPIENT_RETRY_SECONDS,
        retry_delay,
    )

    usual = max(retry_delay(attempt) for attempt in range(1, MAX_ATTEMPTS + 1))
    longer = min(*RECIPIENT_RETRY_SECONDS, *LIMIT_RETRY_SECONDS.values())
    return timedelta(seconds=(usual + longer) / 2)


def read_sending_holds(now):
    """Why Family email is waiting on a sending limit, for Sent mail (#382 M3a).

    Returns None when nothing is, else ``daily_limit`` (a running mail
    sender waits for this server's daily limit), ``gmail_until`` (a running
    sender is held at Gmail's limit; the latest time it names, or None when
    none does), ``gmail_held``, and ``throttled`` and ``throttled_due``: how
    many of the current campaign's Family emails, in the current mode, wait
    to retry on a longer backoff, and when the first of them is due (None
    once that time has passed: the mail sender picks the email up at its
    next pass, so a past time would only mislead). A longer backoff is one
    whose latest outcome was a temporary refusal (``smtp_transient``) and
    whose wait is longer than the ordinary schedule's (``_long_retry``): the
    receiving side throttling every address (#801) or a Gmail limit
    answering that one message. The web login may read every column used
    here; the refused addresses themselves stay in the event's evidence,
    which it may not.

    The sender states come from the same service status rows, grouped the
    same way, as System health's Mail sender panel, so the two pages agree.
    """
    from django.db.models import Count, Exists, F, Min, OuterRef

    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration
    from parishkit.stewardship.system_health import group_processes

    from .outbox_models import OutboxEvent
    from .service_status_models import ServiceStatus

    rows = ServiceStatus.objects.filter(service="mail-dispatch").values(
        "service",
        "process",
        "target",
        "started_at",
        "reported_at",
        "application_version",
        "debug_logging",
        "sender_state",
        "sender_since",
        "sender_until",
    )
    senders = [line for line in group_processes(list(rows), now) if line.running]
    held = [line for line in senders if line.sender_state == "gmail_held"]
    # Only the current campaign's emails in the current mode: a Testing
    # rehearsal's or an earlier campaign's waiting emails are not what the
    # Administrator is looking at.
    configuration = SystemConfiguration.objects.only(
        "mode", "current_campaign_id"
    ).first()
    current = messages().none()
    if configuration is not None and configuration.current_campaign_id:
        current = messages().filter(
            campaign_id=configuration.current_campaign_id, mode=configuration.mode
        )
    throttled = (
        current.filter(
            state="retry_wait",
            family_id__isnull=False,
            not_before__gt=F("updated_at") + _long_retry(),
        )
        .filter(
            Exists(
                OutboxEvent.objects.filter(
                    message_id=OuterRef("pk"),
                    version=OuterRef("version"),
                    reason="smtp_transient",
                )
            )
        )
        .aggregate(count=Count("id"), due=Min("not_before"))
    )
    holds = dict(
        daily_limit=any(line.sender_state == "daily_limit" for line in senders),
        gmail_held=bool(held),
        gmail_until=max(
            (line.sender_until for line in held if line.sender_until), default=None
        ),
        throttled=throttled["count"],
        throttled_due=throttled["due"]
        if throttled["due"] and throttled["due"] > now
        else None,
    )
    if holds["daily_limit"] or holds["gmail_held"] or holds["throttled"]:
        return holds
    return None


def read_refusals(parameters):
    """Refused addresses: one page of the unresolved refusals.

    Returns ``values``, ``window``, ``rows``, ``has_next``, ``total`` and
    ``snapshot``, the current ParishSoft snapshot id the Family sort read
    and the page names its Families from (``with_family_names``). Each row
    has the refusal's ``id``, ``created_at``, ``family_duid`` and
    ``address``, and ``message_id``: the email whose delivery recorded it
    (#935), and ``kind``: ``reported_bounce`` for an Administrator's bounce
    report (#806 slice 4), else ``provider``. ``duid`` keeps one Family's
    refusals.
    """
    values, window = parse_window(parameters, {"duid"}, REFUSAL_SORTING)
    query = unresolved_refusals()
    if values.get("duid"):
        query = query.filter(family_duid=family_duid(values["duid"]))
    total = bounded_count(query)
    # One snapshot read names the page's Families and sorts them, so the
    # order and the shown names agree.
    snapshot = current_snapshot()
    if REFUSAL_SORTING.tokens[values["sort"]][0] == "name":
        query = with_name_keys(query, snapshot, duid="family_duid")
    window, rows, has_next = read_window(
        window,
        REFUSAL_SORTING.order(query, values["sort"]).values(
            "id",
            "created_at",
            "family_duid",
            "address",
            "kind",
            message_id=recording_message(),
        ),
        total,
    )
    return dict(
        values=values,
        window=window,
        rows=rows,
        has_next=has_next,
        total=total,
        snapshot=snapshot,
    )


def read_refusal(refusal_id, parameters):
    """One refusal, its resolution if any, and the source version to verify.

    ``can_clear`` says the current campaign's source generation is the one
    its Family credentials were built from, so a verified clearance can be
    recorded now; while it cannot, ``clear_blocked`` names the first reason
    (``CLEAR_BLOCKS``), which the page shows by its unavailable Clear
    (#1331). The server checks again when Clear is sent. ``message_id`` is
    the email whose delivery recorded the refusal (#935). Raises
    ``RecipientRefusal.DoesNotExist`` for an unknown refusal.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    filters(parameters, allowed=set())
    refusal = RecipientRefusal.objects.annotate(message_id=recording_message()).get(
        pk=refusal_id
    )
    resolved = RecipientRefusalResolution.objects.filter(refusal_id=refusal_id).first()
    source = SourceCurrent.objects.first()
    # Each condition the clearance needs, read apart so the page can say
    # which one is missing: a current campaign, not archived, no other
    # campaign work holding it, and Family credentials built from the
    # current ParishSoft data.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.id IS NOT NULL, c.state='archived', "
            "c.id IS NOT NULL AND stewardship_export_admitted_v1(c.id,true), "
            "EXISTS(SELECT 1 FROM stewardship_source_current cur "
            "WHERE cur.organization_id=%s AND cur.snapshot_id=k.source_snapshot_id "
            "AND cur.generation=k.source_generation AND NOT k.population_dirty) "
            "FROM stewardship_system_configuration r "
            "LEFT JOIN stewardship_campaign c ON c.id=r.current_campaign_id "
            "LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id",
            (refusal.organization_id,),
        )
        current, archived, admitted, fresh = cursor.fetchone() or (
            False,
            False,
            False,
            False,
        )
    blocked = clear_blocked(
        current=current, archived=archived, admitted=admitted, fresh=fresh
    )
    return dict(
        refusal=refusal,
        message_id=refusal.message_id,
        resolved=resolved,
        source=source,
        can_clear=blocked is None,
        clear_blocked=blocked,
        command_id=uuid4(),
    )


# Why a refused address cannot be cleared now (read_refusal), in the order
# the page names them: the first that applies. "busy" covers every refusal
# of the campaign's export admission (stewardship_export_admitted_v1):
# usually campaign work under way, but also no active configuration, no
# credentials row or a held go-live gate, so the page words it as the
# campaign not accepting changes rather than naming one cause.
CLEAR_BLOCKS = ("no_campaign", "archived", "busy", "stale")


def clear_blocked(*, current, archived, admitted, fresh):
    """The first ``CLEAR_BLOCKS`` reason a clearance is refused now, or None."""
    for reason, holds in zip(
        CLEAR_BLOCKS, (not current, archived, not admitted, not fresh), strict=True
    ):
        if holds:
            return reason
    return None
