"""Outgoing mail and refused address reads, moved out of the delivery views.

The Admin pages (``jobs.delivery_views``) and the Admin automation command
line (``delivery list``, ``delivery show``, ``delivery refusals`` and
``delivery refusal-show``, ADM-11 PR 9b) read through these functions, so
both see the same rows, filters, sort orders and offered actions. Nothing
here takes a request or checks authority: each caller admits first, reads
inside its own transaction and rechecks afterwards. ``parameters`` is the
page's query (a ``QueryDict``); repeated, unknown or malformed options are
refused (``ValueError``) as on the page.
"""

from datetime import timedelta
from uuid import uuid4

from django.db import DatabaseError, connection, transaction
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters
from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .delivery_metadata import (
    DELIVERY_SORTING,
    FIELDS,
    family_duid,
    listing,
    messages,
    with_name_keys,
)

# Every Refused addresses column but Email sorts on the server; the default
# keeps the old Family DUID, then address, order. Family (name) sorts as
# Outgoing mail's does: surname, then the whole shown name, then Family DUID
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
# What each action offered on one delivery's page is called there.
ACTION_LABELS = {
    "note": _("Save evidence note"),
    "accept": _("Confirm delivery using external evidence"),
    "confirm_unsent": _("Record that the provider did not send it (no resend)"),
    "resend": _("Authorize potentially duplicate resend"),
    "retry_failed": _("Retry failed delivery"),
    "retry_unsent": _("Retry delivery not accepted by the provider"),
}


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


def read_listing(parameters):
    """Outgoing mail: one filtered page of messages, never their content.

    Returns ``values`` (the validated filters), ``window``, ``rows``,
    ``has_next``, ``total`` (a ``bounded_count``), ``send`` (the described
    Family email send it is limited to, or None) and ``snapshot``: the
    current ParishSoft snapshot id the Family sort read, which the page
    passes to ``with_family_names`` so the shown names match the order (or
    None before the first ParishSoft sync). A malformed or
    unknown send is refused rather than ignored, so a filtered link never
    silently lists every email instead (#432).
    """
    from .send_history import SendKey, describe

    values, window = parse_window(parameters, {"state", "q", "send"}, DELIVERY_SORTING)
    state, query = values.get("state", "all"), values.get("q", "")
    send = described = None
    if "send" in values:
        send = SendKey.parse(values["send"])
        described = describe(send)
        if described is None:
            raise ValueError("Unknown Family email send.")
    snapshot = current_snapshot()
    window, rows, has_next, total = listing(
        window,
        state=state,
        query=query,
        sort=values["sort"],
        send=send,
        snapshot=snapshot,
    )
    return dict(
        values=values | {"state": state, "q": query},
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
    ``snapshot_family_names`` as the Family timeline names a Family, or
    None for an Administrator report and for a Family that snapshot lacks.
    The email itself stores no name, so the names are read for the shown
    page only: at most two small queries (its Families and their heads),
    however many rows. The command line's ``delivery list`` does not read
    them. ``key`` names each row's Family DUID: an email's
    ``family__family_duid`` on Outgoing mail, a refusal's ``family_duid`` on
    Refused addresses (#935).
    """
    from parishkit.stewardship.source.snapshot_names import snapshot_family_names

    duids = {row[key] for row in rows} - {None}
    names = snapshot_family_names(snapshot, duids, "Family")
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

    Outgoing mail marks such an email ("1 address refused", #935): a refused
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
    Outgoing mail (``delivery_bulk``) select by these same rules, so a bulk
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


def read_detail(message_id, parameters):
    """One delivery: its metadata, history, latest task, notes and actions.

    The history's upper version is pinned to the message read, so a page
    and its history agree. ``actions`` are the resolutions the page offers
    now, in its order; ``retry_unavailable`` says a retry would be offered
    but the campaign's resend admission refuses it. Raises
    ``OutboxMessage.DoesNotExist`` for an unknown message.
    """
    from parishkit.stewardship.campaigns.models import Campaign

    from .delivery_resolution_models import DeliveryResolution
    from .models import TaskRun
    from .outbox_models import OutboxEvent

    window = parse_window(parameters, set())[1]
    message = messages().values(*FIELDS).get(pk=message_id)
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
    campaign = Campaign.objects.only("state").get(pk=message["campaign_id"])
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
        campaign_state=campaign.state,
        can_resolve=can_resolve,
        can_retry=can_retry,
    )
    notes, notes_following = window.rows(
        DeliveryResolution.objects.filter(message_id=message_id)
        .order_by("-created_at", "-id")
        .values("created_at", "action", "evidence_note")
    )
    return dict(
        delivery=message,
        events=events,
        task=task,
        notes=notes,
        window=window,
        has_next=events_following or notes_following,
        actions=actions,
        retry_unavailable=bool(
            task
            and task["state"] == "failed"
            and not can_retry
            and message["state"]
            in {"delivery_unknown", "permanent_failure", "pending", "retry_wait"}
        ),
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
    """Why Family email is waiting on a sending limit, for Outgoing mail (#382 M3a).

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
    recorded now. ``message_id`` is the email whose delivery recorded the
    refusal (#935). Raises ``RecipientRefusal.DoesNotExist`` for an unknown
    refusal.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    filters(parameters, allowed=set())
    refusal = RecipientRefusal.objects.annotate(message_id=recording_message()).get(
        pk=refusal_id
    )
    resolved = RecipientRefusalResolution.objects.filter(refusal_id=refusal_id).first()
    source = SourceCurrent.objects.first()
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM stewardship_source_current cur "
            "JOIN stewardship_system_configuration r ON true "
            "JOIN stewardship_campaign c ON c.id=r.current_campaign_id "
            "JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id "
            "WHERE cur.organization_id=%s AND cur.snapshot_id=k.source_snapshot_id "
            "AND cur.generation=k.source_generation AND NOT k.population_dirty "
            "AND c.state<>'archived' "
            "AND stewardship_export_admitted_v1(c.id,true))",
            (refusal.organization_id,),
        )
        can_clear = cursor.fetchone()[0]
    return dict(
        refusal=refusal,
        message_id=refusal.message_id,
        resolved=resolved,
        source=source,
        can_clear=can_clear,
        command_id=uuid4(),
    )
