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

from django.db import connection
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.web.contracts import PageWindow, expected_version, filters
from parishkit.stewardship.web.tables import Sorting, bounded_count, read_window

from .delivery_metadata import DELIVERY_SORTING, FIELDS, family_duid, listing, messages

# Every Refused addresses column sorts on the server; the default keeps the
# old Family DUID, then address, order. No index orders these keys (the
# recipient_refusal_identity index leads with organization_id, then
# family_duid), so each is a top-N sort over the unresolved refusals, a
# small set (one row per refused Family address). id is the unique
# tiebreak.
REFUSAL_SORTING = Sorting.by_column(
    {
        "address": ("address",),
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
    ``has_next``, ``total`` (a ``bounded_count``) and ``send`` (the
    described Family email send it is limited to, or None). A malformed or
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
    window, rows, has_next, total = listing(
        window, state=state, query=query, sort=values["sort"], send=send
    )
    return dict(
        values=values | {"state": state, "q": query},
        window=window,
        rows=rows,
        has_next=has_next,
        total=total,
        send=described,
    )


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
    actions = ["note"] if campaign.state != "archived" and can_resolve else []
    if task and task["state"] == "failed" and actions:
        if message["state"] == "delivery_unknown":
            # Both settle the attempt from external evidence without a
            # send, so neither depends on the resend admission (can_retry).
            actions += ["accept", "confirm_unsent"]
            if can_retry:
                actions.append("resend")
        elif can_retry:
            if message["state"] == "permanent_failure":
                actions.append("retry_failed")
            elif message["state"] in {"pending", "retry_wait"}:
                actions.append("retry_unsent")
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

    Returns ``values``, ``window``, ``rows`` (the refusal records),
    ``has_next`` and ``total``. ``duid`` keeps one Family's refusals.
    """
    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    values, window = parse_window(parameters, {"duid"}, REFUSAL_SORTING)
    query = RecipientRefusal.objects.exclude(
        pk__in=RecipientRefusalResolution.objects.values("refusal_id")
    )
    if values.get("duid"):
        query = query.filter(family_duid=family_duid(values["duid"]))
    total = bounded_count(query)
    window, rows, has_next = read_window(
        window, REFUSAL_SORTING.order(query, values["sort"]), total
    )
    return dict(values=values, window=window, rows=rows, has_next=has_next, total=total)


def read_refusal(refusal_id, parameters):
    """One refusal, its resolution if any, and the source version to verify.

    ``can_clear`` says the current campaign's source generation is the one
    its Family credentials were built from, so a verified clearance can be
    recorded now. Raises ``RecipientRefusal.DoesNotExist`` for an unknown
    refusal.
    """
    from parishkit.stewardship.source.snapshot_models import SourceCurrent

    from .recipient_models import RecipientRefusal, RecipientRefusalResolution

    filters(parameters, allowed=set())
    refusal = RecipientRefusal.objects.get(pk=refusal_id)
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
        resolved=resolved,
        source=source,
        can_clear=can_clear,
        command_id=uuid4(),
    )
