"""The Report emails page's rows, read once for the page and the command line.

The report-emails spec (#1786, "Report list" and "Past sends") lists each
report email of the current campaign: its name, its schedule in plain
words, its recipients in short, its next and last send, a status and the
actions its status allows (the spec's action matrix). Each report's past
sends are listed with it, newest first, and the deleted reports that sent
something are listed apart, so history is never lost.

Everything here is read from the applied configuration (the report values)
and from bindings only: preparations, snapshot and recipient bindings,
outbox states and task states, never a send's compiled content. The one
exception is a list block's count ("12 open requests"), read from the
snapshot's block data, which the saved copy page reads anyway.

Nothing here takes a request or checks authority; ``report_email_views``
and ``admin_report_emails`` admit first and call ``listing`` inside their
own read snapshot.
"""

from datetime import UTC, date, datetime
from uuid import UUID

from django.db.models import OuterRef, Subquery
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from parishkit.stewardship.accounts.models import AddressRule
from parishkit.stewardship.campaigns.models import ScheduleRevision
from parishkit.stewardship.campaigns.schedule_evaluation import ReportPlan
from parishkit.stewardship.campaigns.schedule_models import (
    ScheduleDefinition,
    ScheduleOccurrence,
)
from parishkit.stewardship.jobs.models import TaskRun
from parishkit.stewardship.wall_clock import WEEKDAYS

from .report_blocks import REGISTRY
from .report_blocks.registry import LIST
from .report_email_cohort import resolve_cohort
from .report_email_models import (
    ReportEmailPreparation,
    ReportEmailRecipient,
    ReportEmailSnapshot,
)

# Each report lists this many of its newest past sends (spec, "Past sends").
SENDS_SHOWN = 20

UPCOMING, SENDING, PAUSED, DONE, ATTENTION = (
    "upcoming",
    "sending",
    "paused",
    "done",
    "attention",
)
STATUS_LABELS = {
    UPCOMING: _("Upcoming"),
    SENDING: _("Sending"),
    PAUSED: _("Paused"),
    DONE: _("Done"),
    ATTENTION: _("Needs attention"),
}
# A past send's result, in the spec's words.
SENT, NOTHING, NOBODY, FAILED, UNCERTAIN, IN_PROGRESS = (
    "sent",
    "nothing",
    "nobody",
    "failed",
    "uncertain",
    "sending",
)
RESULT_LABELS = {
    SENT: _("Sent"),
    NOTHING: _("Not sent: nothing to report"),
    NOBODY: _("Not sent: no current recipients"),
    FAILED: _("Failed"),
    UNCERTAIN: _("Uncertain: check Sent mail"),
    IN_PROGRESS: _("Sending"),
}
HOW_LABELS = {
    "scheduled": _("Scheduled"),
    "manual": _("Send now"),
    "recovery": _("Recovery"),
}

# Why an action of the matrix is unavailable, said beside the greyed icon.
IN_PROGRESS_REASON = _("A send of this report is in progress")
ATTENTION_REASON = _("A send needs attention on Sent mail first")
NOTHING_AHEAD = _("No send is ahead to pause")
ARCHIVED_REASON = _("The campaign is archived")

# Task states of a send still being prepared, and those that stopped it.
_ACTIVE_TASKS = frozenset({"queued", "running", "retry_wait"})
_STOPPED_TASKS = frozenset({"abandoned", "failed"})
_FINAL_MESSAGES = frozenset({"delivered", "permanent_failure", "cancelled"})
_OPEN_PHASES = frozenset({"dates", "cover", "inputs", "capture", "fanout"})

GROUP_LABELS = {
    "administrators": _("All Administrators"),
    "staff": _("All Staff"),
}
FROM_LABELS = {
    "campaign_start": _("from the campaign's first day"),
    "day_after_campaign_start": _("from the day after the campaign starts"),
}
UNTIL_LABELS = {
    "campaign_end": _("until the campaign's last day"),
    "day_after_campaign_end": _("until the day after it ends"),
    "first_send_after_campaign_end": _("until the first send after it ends"),
    "archive": _("until the campaign is archived"),
}


def report_records(configuration, campaign_id):
    """The campaign's report emails in the applied configuration, by name.

    Each is ``{"id", "values"}``, ``id`` the record's (and its schedule
    definition's) stable UUID as text.
    """
    rows = [
        {"id": row["id"], "values": row["values"]}
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("schedules", [])
        if row["values"].get("kind") == "report"
        and row["values"]["campaign_id"] == str(campaign_id)
    ]
    return sorted(rows, key=lambda row: (row["values"]["name"].casefold(), row["id"]))


def user_names(configuration):
    """Each Admin portal user's name (or address), by record id."""
    return {
        row["id"]: row["values"].get("name") or row["values"].get("email", "")
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("login_rules", [])
        if row["values"].get("kind") == "user"
    }


def plan(values, campaign):
    """The report's schedule evaluator against the campaign's values."""
    return ReportPlan.from_values(values, campaign)


def paused_since(values):
    """When the report's open pause began (UTC), or None while not paused."""
    ranges = values["paused_ranges"]
    if ranges and ranges[-1]["end"] is None:
        return datetime.fromisoformat(ranges[-1]["start"])
    return None


def next_send(values, campaign, now):
    """The report's next send after ``now`` (a ``ScheduleSlot``), or None.

    A paused report has none: its sends stay off until Resume.
    """
    return plan(values, campaign).next_slot(after=now)


def resumed_send(values, campaign, now):
    """The send a Resume applied now would make next, or None.

    The open pause is closed at ``now``, as Resume closes it (spec, "Pause
    and resume"), so a send due inside the pause is not offered.
    """
    ranges = list(values["paused_ranges"])
    if ranges and ranges[-1]["end"] is None:
        if now <= datetime.fromisoformat(ranges[-1]["start"]):
            # Paused this very moment: nothing fell inside the pause yet.
            ranges.pop()
        else:
            ranges[-1] = ranges[-1] | {"end": now.astimezone(UTC).isoformat()}
    return next_send(values | {"paused_ranges": ranges}, campaign, now)


def schedule_words(values, campaign, now):
    """The report's schedule in plain words, as ordered segments.

    Each segment is ``("text", words)``, ``("date", date)`` (a calendar
    date, drawn in the parish's format), ``("time", instant)`` (an instant
    drawn as this browser's clock time) or ``("instant", instant)`` (drawn
    as this browser's date and time); text segments carry their own
    spacing, so the segments are drawn side by side. The time is that of
    the report's next send (else its first), so it reads in the reader's
    zone. "Every day at 7:00 AM, from the day after the campaign starts
    until the day after it ends."; "Once, <date and time>".
    """
    schedule = values["schedule"]
    evaluator = plan(values, campaign)
    if schedule["type"] == "once":
        return [("text", f"{_('Once')}, "), ("instant", evaluator.one_time_due)]
    # Any send's time will do to write the clock time; skipped and paused
    # sends count too, so a paused report still names its time.
    unpaused = plan(values | {"paused_ranges": [], "skip_dates": []}, campaign)
    upcoming = unpaused.next_slot(after=now) or next(
        iter(unpaused.page(through=datetime.max.replace(tzinfo=UTC), limit=1).slots),
        None,
    )
    if schedule["frequency"] == "daily":
        lead = _("Every day at")
    else:
        days = [str(WEEKDAYS[day]) for day in schedule["weekdays"]]
        lead = _("Every %(days)s at") % {"days": _join(days)}
    segments = [("text", f"{lead} ")]
    if upcoming is not None:
        segments.append(("time", upcoming.due_at))
    else:
        segments.append(("text", schedule["time"][:5]))
    segments += _bound(schedule["from"], FROM_LABELS, _("from"), ", ")
    segments += _bound(schedule["until"], UNTIL_LABELS, _("until"), " ")
    segments.append(("text", "."))
    return segments


def _bound(value, labels, word, before):
    """One repeat bound's segments: its words, or the word and a chosen date."""
    if value in labels:
        return [("text", f"{before}{labels[value]}")]
    return [("text", f"{before}{word} "), ("date", date.fromisoformat(value))]


def _join(names):
    """Names joined in plain English: one alone, two with "and", more with commas."""
    if len(names) < 3:
        return " and ".join(names)
    return ", ".join(names[:-1]) + ", and " + names[-1]


def schedule_text(segments):
    """The schedule's words as one line of text, for the command line.

    Times are given as UTC instants and dates as ISO dates, since a
    command has no browser zone to convert them to.
    """
    parts = []
    for kind, value in segments:
        if kind == "text":
            parts.append(str(value))
        elif kind == "date":
            parts.append(value.isoformat())
        else:
            parts.append(value.astimezone(UTC).isoformat())
    return "".join(parts)


def entry_label(entry, names):
    """How one recipient entry is named: a group, a user or an address."""
    ((kind, value),) = entry.items()
    if kind == "group":
        return str(GROUP_LABELS[value])
    if kind == "user":
        return names.get(value) or str(_("a removed user"))
    return value


def recipients_short(values, names):
    """The recipients in short: "All Administrators and 2 others"."""
    entries = values["recipients"]
    first = entry_label(entries[0], names)
    others = len(entries) - 1
    if not others:
        return first
    return ngettext(
        "%(first)s and %(count)s other", "%(first)s and %(count)s others", others
    ) % {"first": first, "count": others}


def _how(slot):
    """How a send was made, from its occurrence's slot.

    A manual send has its ``manual:`` occurrence from the moment it is
    requested; a scheduled send has none while it is still finding its
    slots, so no slot means scheduled.
    """
    if slot is None:
        return "scheduled"
    if slot.startswith("manual:"):
        return "manual"
    if slot.startswith("recovery:"):
        return "recovery"
    return "scheduled"


def _list_count(blocks):
    """A list block's item count in a snapshot's block data, or None."""
    for entry in blocks or ():
        block = REGISTRY.get(entry.get("block"))
        if block is not None and block.kind == LIST and entry.get("data"):
            return len(entry["data"].get("items", ()))
    return None


def _result(phase, task_state, skipped, messages):
    """One send's result from its phase, task and message states.

    ``skipped`` says the send completed with nothing to report (no block
    had content and the report sends only then); a completed send that
    made no message otherwise had no current recipient.
    """
    if phase in _OPEN_PHASES:
        return FAILED if task_state in _STOPPED_TASKS else IN_PROGRESS
    if not messages:
        return NOTHING if skipped else NOBODY
    states = set(messages)
    if states - _FINAL_MESSAGES:
        return UNCERTAIN if "delivery_unknown" in states else IN_PROGRESS
    if "permanent_failure" in states:
        return FAILED
    return SENT if "delivered" in states else NOBODY


def report_sends(campaign_id, mode, definition_ids=None, *, limit=SENDS_SHOWN):
    """Each report's newest sends, by definition id (text), newest first.

    ``definition_ids`` limits the reports read (None: every report of the
    campaign). A send is ``{"id", "kind", "made_at", "how", "as_of",
    "recipients", "result", "count", "name"}``: ``kind`` is ``report`` for
    a report email send (``id`` its preparation's, which names its saved
    copy) and ``daily``/``weekly`` for a digest the report was converted
    from (``id`` the digest snapshot's); ``recipients`` counts the
    messages made; ``count`` is a list block's item count (None without
    one); ``name`` is the report's name as the send's revision had it. A
    cancelled preparation is no send and is left out. Only ``mode``'s
    sends are listed: a Testing rehearsal's are not Production history.
    """
    query = ReportEmailPreparation.objects.filter(campaign_id=campaign_id, mode=mode)
    if definition_ids is not None:
        query = query.filter(definition_id__in=list(definition_ids))
    # A cancelled preparation is no send, and neither is one that completed
    # without an occurrence: it found no slot to cover (after a revision
    # change, say).
    rows = list(
        query.exclude(phase="cancelled")
        .exclude(phase="complete", occurrence_id__isnull=True)
        .annotate(
            slot=Subquery(
                ScheduleOccurrence.objects.filter(pk=OuterRef("occurrence_id")).values(
                    "slot"
                )[:1]
            ),
            # The newest run of the send's task chain: a retry is a new run
            # of the same root (the Send now guard reads it the same way).
            task_state=Subquery(
                TaskRun.objects.filter(root_id=OuterRef("task_id"))
                .order_by("-retry_sequence")
                .values("state")[:1]
            ),
            name=Subquery(
                ScheduleRevision.objects.filter(pk=OuterRef("revision_id")).values(
                    "values__name"
                )[:1]
            ),
            content_required=Subquery(
                ScheduleRevision.objects.filter(pk=OuterRef("revision_id")).values(
                    "values__content_required"
                )[:1]
            ),
        )
        .order_by("-created_at", "-id")
        .values(
            "id",
            "definition_id",
            "phase",
            "created_at",
            "as_of",
            "slot",
            "task_state",
            "name",
            "content_required",
        )
    )
    shown = {}
    for row in rows:
        kept = shown.setdefault(str(row["definition_id"]), [])
        if len(kept) < limit:
            kept.append(row)
    chosen = [row["id"] for kept in shown.values() for row in kept]
    snapshots = {
        row["preparation_id"]: row
        for row in ReportEmailSnapshot.objects.filter(preparation_id__in=chosen).values(
            "preparation_id", "has_content", "created_at", "blocks"
        )
    }
    messages = {}
    for row in ReportEmailRecipient.objects.filter(
        snapshot__preparation_id__in=chosen, outbox_id__isnull=False
    ).values("snapshot__preparation_id", "outbox__state"):
        messages.setdefault(row["snapshot__preparation_id"], []).append(
            row["outbox__state"]
        )
    result = {}
    for definition, kept in shown.items():
        result[definition] = [
            _send(row, snapshots.get(row["id"]), messages.get(row["id"], []))
            for row in kept
        ]
    _add_digest_sends(result, campaign_id, definition_ids, limit)
    return result


def _send(row, snapshot, states):
    """One report email send's row (``report_sends``)."""
    return {
        "id": row["id"],
        "kind": "report",
        "made_at": snapshot["created_at"] if snapshot else row["created_at"],
        "how": _how(row["slot"]),
        "as_of": row["as_of"],
        "recipients": len(states),
        "result": _result(
            row["phase"],
            row["task_state"],
            # Send now always sends, so only a scheduled send is skipped.
            bool(
                snapshot
                and not snapshot["has_content"]
                and row["content_required"]
                and _how(row["slot"]) != "manual"
            ),
            states,
        ),
        "count": _list_count(snapshot["blocks"]) if snapshot else None,
        "name": row["name"],
        "copy": snapshot is not None and bool(states),
    }


def _add_digest_sends(result, campaign_id, definition_ids, limit):
    """Add the Daily and Weekly digests a converted report sent before it was one.

    The conversion kept each digest's definition UUID (spec, "Conversion"),
    so a digest snapshot names its report through its preparation. They
    are older than every report send, so they follow them, newest first.
    """
    from .digest_models import DailyDigestReady, DailyDigestSnapshot
    from .weekly_models import WeeklyDigestSnapshot, WeeklyManualRequest

    daily = DailyDigestSnapshot.objects.filter(
        campaign_id=campaign_id,
        pk__in=DailyDigestReady.objects.values("snapshot_id"),
    )
    weekly = WeeklyDigestSnapshot.objects.filter(campaign_id=campaign_id)
    if definition_ids is not None:
        daily = daily.filter(preparation__definition_id__in=list(definition_ids))
        weekly = weekly.filter(preparation__definition_id__in=list(definition_ids))
    manual = set(
        WeeklyManualRequest.objects.filter(campaign_id=campaign_id).values_list(
            "pk", flat=True
        )
    )
    old = [
        (
            row["preparation__definition_id"],
            {
                "id": row["pk"],
                "kind": "daily",
                "made_at": row["observed_at"],
                "how": "recovery" if len(row["covered_dates"]) > 1 else "scheduled",
                "as_of": None,
                "through_date": row["through_date"],
                "recipients": None,
                "result": SENT,
                "count": None,
                "name": None,
                "copy": True,
            },
        )
        for row in daily.values(
            "pk",
            "observed_at",
            "through_date",
            "covered_dates",
            "preparation__definition_id",
        )
    ] + [
        (
            row["preparation__definition_id"],
            {
                "id": row["pk"],
                "kind": "weekly",
                "made_at": row["observed_at"],
                "how": "manual" if row["preparation_id"] in manual else "scheduled",
                "as_of": row["observed_at"],
                "recipients": None,
                "result": SENT,
                "count": None,
                "name": None,
                "copy": True,
            },
        )
        for row in weekly.values(
            "pk", "observed_at", "preparation_id", "preparation__definition_id"
        )
    ]
    old.sort(key=lambda item: item[1]["made_at"], reverse=True)
    for definition, row in old:
        kept = result.setdefault(str(definition), [])
        if len(kept) < limit:
            kept.append(row)


def _open_send(sends):
    """The status a report's newest send gives it, or None when it is settled."""
    if not sends:
        return None
    result = sends[0]["result"]
    if result == IN_PROGRESS:
        return SENDING
    if result in (FAILED, UNCERTAIN):
        return ATTENTION
    return None


def status(values, sends, upcoming):
    """A report's status (spec, "Report list"), most pressing first.

    ``sends`` are its past sends (``report_sends``), newest first, and
    ``upcoming`` its next send or None.
    """
    pressing = _open_send(sends)
    if pressing is not None:
        return pressing
    if paused_since(values) is not None:
        return PAUSED
    return UPCOMING if upcoming is not None else DONE


def actions(row_status, *, archived, editable):
    """Each row action's availability: ``{action: None or why not}``.

    The spec's action matrix. ``editable`` says whether the row offers
    Edit (every report does since the report editor, delivery plan item
    6). An absent key is an action the row does not offer at all.
    """
    if archived:
        return {}
    blocked = {
        SENDING: IN_PROGRESS_REASON,
        ATTENTION: ATTENTION_REASON,
    }.get(row_status)
    offered = {
        "send_now": blocked,
        "resume" if row_status == PAUSED else "pause": blocked
        or (NOTHING_AHEAD if row_status == DONE else None),
        "delete": blocked,
    }
    if editable:
        offered["edit"] = blocked
    return offered


def _dialog_text(values, revision, recipients, resumes):
    """What a row's Send now and Resume dialogs say and post.

    Send now posts the revision the page drew (``send_with``; a changed
    report is refused as stale) and names how many emails it makes and,
    with the open requests block, that it lists requests earlier sends
    listed (spec, "Send now"). Resume names the day of the send it makes
    next: the instant in ``resume_body`` is written as this browser's day
    (``data-local-day``, date-format-v1.js).
    """
    listed = any(
        entry["block"] == "additional_information" for entry in values["blocks"]
    )
    body = ngettext(
        "It is sent now to its %(count)s recipient, outside its schedule, and "
        "recorded as a Send now, not a scheduled send. Its schedule is "
        "unchanged.",
        "It is sent now to its %(count)s recipients, outside its schedule, and "
        "recorded as a Send now, not a scheduled send. Its schedule is "
        "unchanged.",
        recipients,
    ) % {"count": recipients}
    if listed:
        body += " " + str(
            _(
                "It lists every open additional information request, including "
                "requests earlier sends listed."
            )
        )
    text = {
        "send_with": f"revision={revision}" if revision else None,
        "send_body": body,
        "resume_body": None,
        "resume_day": None,
    }
    if resumes is not None:
        stamp = resumes.due_at.astimezone(UTC).isoformat()
        text["resume_day"] = stamp
        text["resume_body"] = str(
            _(
                "Its next send is then on %(day)s. Sends missed while it was "
                "paused are not made."
            )
            % {"day": stamp}
        )
    return text


def report_rows(configuration, campaign, now, sends, *, administrator):
    """The page's rows for ``campaign`` in its order: next send, then name.

    Each row holds the report's id, name, values, schedule words, recipients
    in short (Administrators only), next send, last send, status, paused
    instant, the next send a Resume would make, its past sends and its
    actions (Administrators only). Reports with no send ahead come last.
    ``sends`` are the campaign's past sends by report (``report_sends``).
    """
    values_of = campaign.active_configuration.values
    archived = campaign.state == "archived"
    records = report_records(configuration, campaign.pk)
    names = user_names(configuration) if administrator else {}
    revisions = dict(
        ScheduleDefinition.objects.filter(
            pk__in=[row["id"] for row in records]
        ).values_list("pk", "current_revision_id")
    )
    rules = (
        list(
            AddressRule.objects.filter(
                configuration_id=configuration.active_configuration_id
            ).values_list("record_id", "email", "roles")
        )
        if administrator
        else []
    )
    rows = []
    for record in records:
        values = record["values"]
        upcoming = next_send(values, values_of, now)
        past = sends.get(record["id"], [])
        row_status = status(values, past, upcoming)
        resumes = resumed_send(values, values_of, now) if row_status == PAUSED else None
        dialogs = (
            _dialog_text(
                values,
                revisions.get(UUID(record["id"])),
                len(resolve_cohort(values, rules)),
                resumes,
            )
            if administrator
            else {}
        )
        rows.append(
            {
                "id": record["id"],
                "name": values["name"],
                "values": values,
                "schedule": schedule_words(values, values_of, now),
                "recipients": recipients_short(values, names)
                if administrator
                else None,
                "recipient_count": len(values["recipients"]),
                "next": upcoming,
                "last": past[0] if past else None,
                "status": row_status,
                "status_label": STATUS_LABELS[row_status],
                "paused_since": paused_since(values),
                "resumes": resumes,
                "sends": past,
                "has_list_block": any(
                    REGISTRY[entry["block"]].kind == LIST for entry in values["blocks"]
                ),
                "actions": actions(row_status, archived=archived, editable=True)
                if administrator
                else {},
            }
            | dialogs
        )
    far = datetime.max.replace(tzinfo=UTC)
    rows.sort(
        key=lambda row: (
            row["next"].due_at if row["next"] else far,
            row["name"].casefold(),
        )
    )
    return rows


def deleted_reports(sends, kept_ids):
    """The deleted reports that sent something, each with its past sends.

    ``sends`` are the campaign's past sends by report (``report_sends``)
    and ``kept_ids`` the reports still in the configuration. A deleted
    report is named as its newest send's revision named it.
    """
    rows = []
    for definition, past in sends.items():
        if definition in kept_ids or not past:
            continue
        name = next((send["name"] for send in past if send["name"]), None)
        rows.append({"id": definition, "name": name or "", "sends": past})
    return sorted(rows, key=lambda row: (row["name"].casefold(), row["id"]))


def listing(configuration, campaign, mode, now, *, administrator):
    """The page's whole view of ``campaign``: its reports and deleted ones."""
    sends = report_sends(campaign.pk, mode)
    rows = report_rows(configuration, campaign, now, sends, administrator=administrator)
    return {
        "rows": rows,
        "deleted": deleted_reports(sends, {row["id"] for row in rows}),
        "archived": campaign.state == "archived",
    }
