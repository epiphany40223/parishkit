"""Pause, Resume, Delete, Skip and Unskip sends of a report email (#1786).

The Report emails page's row actions and ``pk-admin report-email
preview``/``confirm`` build the same change here (report-emails spec,
"Pause and resume", "Delete a report email", "Skip and unskip sends"):
each is an
ordinary campaign configuration change to the report's ``report`` schedule
record, built, validated against the whole candidate, signed with the
campaign's work fingerprint and recorded under the work lock exactly as
every schedule change is (``admin_editing``). Nothing here takes a request
or checks authority; the callers admit first and run ``build_review``
inside their own work transaction.

- **Pause** adds an open pause interval and **Resume** closes it, each at
  the database's clock when the change is reviewed (a signed change cannot
  name the moment of its own Apply); Apply is refused once any work of the
  report changed since, so no send falls between the two unnoticed.
- **Delete** removes the record; the definition keeps its history.
- **Skip** adds the slot keys of one or more sends to ``skip_dates``, each
  send checked on its own: only a send of the report that is still ahead, not skipped
  already and without Production work. **Unskip** removes them again, for a
  send still ahead that is skipped now (a past skip is kept on every
  revision). Each send's time is signed into the review, so Apply refuses
  once any of them has passed.

While a send of the report is in progress or its result is uncertain the
work summary counts it as blocking, and the review then carries no signed
preview (the caller refuses it, naming Sent mail).
"""

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.accounts.admin_editing import database_now, sign_preview
from parishkit.stewardship.accounts.campaign_views import _state
from parishkit.stewardship.accounts.request_patch import build_candidate
from parishkit.stewardship.accounts.schedule_preview import fingerprint, work_summary
from parishkit.stewardship.campaigns.models import ScheduleOccurrence
from parishkit.stewardship.campaigns.report_values import PAUSE_LIMIT, REPORT_LIMIT
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.refusals import (
    UserFacingDenied,
    UserFacingError,
    UserFacingStale,
    stale_page,
    unexpected_fields,
)

from .report_email_list import plan, report_records

OPERATIONS = ("create", "edit", "pause", "resume", "delete", "skip", "unskip")
# A change document names at most one entry per report, and a campaign has
# at most twenty reports (report_values.REPORT_LIMIT).
ENTRY_LIMIT = 20
# The most sends one skip or unskip entry names: the page offers the next
# five; the command line may name more.
SENDS_LIMIT = 50

ARCHIVED = _("The campaign is archived, so its report emails can no longer change.")
ALREADY_PAUSED = _("This report email is already paused.")
NOT_PAUSED = _("This report email is not paused.")
TOO_MANY_PAUSES = _(
    "This report email has been paused %(limit)s times, the most it can be. "
    "Delete it and add it again to pause it once more."
)
SKIP_PASSED = _(
    "That send of the report email is already past, so it can't be skipped."
)
SKIP_WORKED = _(
    "That send of the report email has already been prepared or sent, so it "
    "can't be skipped."
)
UNSKIP_PASSED = _(
    "That skipped send of the report email is already past, so it can't be sent again."
)
UNSKIP_WORKED = _(
    "That send of the report email has already been prepared or sent, so its "
    "skip can't be removed."
)
DOES_NOT_FIT = _("This change can't be made to the report email as it is saved now.")
HELD = _("This campaign's report emails can't be changed right now.")
BLOCKED = _(
    "A send of this report email is in progress or has an uncertain result, "
    "so it can't be changed yet."
)
BLOCKED_FIX = _("Wait for it to finish, or resolve it on the Sent mail page.")


def preview_salt(campaign_id):
    """The signing salt of one campaign's report email changes.

    Apart from the mail schedules' salt, so a report email review never
    confirms as a schedule change or the other way round.
    """
    return f"stewardship-report-emails-preview-v1:{campaign_id}"


def refuse_archived(campaign):
    """Refuse any change to an archived campaign's report emails."""
    if campaign.state == "archived":
        raise UserFacingDenied(ARCHIVED, fix=_("Nothing was changed."))


def change_state(service, campaign_id):
    """Pin the applied configuration and find the campaign a change names.

    Returns ``(state, campaign)``, ``state`` being ``campaign_views._state``.
    An unknown campaign is a ``LookupError``; one that is not current, or
    that background work holds (a mail send or Testing cleanup), is
    refused as the Dates and mail schedules page refuses it.
    """
    state = _state(service)
    configuration, held = state[0], state[3]
    campaign = next((row for row in state[1] if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    refuse_archived(campaign)
    if held or configuration.current_campaign_id != campaign.pk:
        raise UserFacingStale(
            HELD,
            fix=_(
                "Only the current campaign's report emails can change, and not "
                "while mail delivery or other background work is running. Try "
                "again later."
            ),
        )
    return state, campaign


def confirm_scope(service, campaign_id):
    """What a confirmation rechecks under the work lock: the version and work.

    The campaign is refused once archived, as at review.
    """
    state = _state(service)
    campaign = next((row for row in state[1] if row.pk == campaign_id), None)
    if campaign is not None:
        refuse_archived(campaign)
    return state[0], work_fingerprint(state, campaign_id, work_summary(campaign_id))


def report_work(campaign_id):
    """The campaign's report sends still being prepared, by id and phase.

    The shared work summary counts a send's occurrences and messages, which
    a send has only once its preparation has planned them; a preparation
    the scheduler has just allocated is work too, so the review's
    fingerprint names these as well.
    """
    from .report_email_models import ReportEmailPreparation

    return sorted(
        [str(identifier), phase]
        for identifier, phase in ReportEmailPreparation.objects.filter(
            campaign_id=campaign_id
        )
        .exclude(phase__in=("complete", "cancelled"))
        .values_list("id", "phase")
    )


def work_fingerprint(state, campaign_id, summary):
    """The review's binding to the campaign's scope and its report work."""
    return fingerprint(state[-1], [summary, report_work(campaign_id)])


def _keys(operation):
    """The members an entry of ``operation`` has."""
    return {
        "create": {"operation", "values"},
        "edit": {"operation", "id", "values"},
        "skip": {"operation", "id", "sends"},
        "unskip": {"operation", "id", "sends"},
    }.get(operation, {"operation", "id"})


def _slots(sends):
    """A skip or unskip entry's slot keys: 1 to ``SENDS_LIMIT`` canonical ISO
    dates, each listed once (a ``ValueError`` otherwise)."""
    if type(sends) is not list or not 1 <= len(sends) <= SENDS_LIMIT:
        raise ValueError("Sends are a list of slot keys.")
    keys = []
    for send in sends:
        if type(send) is not str or date.fromisoformat(send).isoformat() != send:
            raise ValueError("A send is a canonical slot key.")
        keys.append(send)
    if len(set(keys)) != len(keys):
        raise ValueError("A send is listed once.")
    return keys


def parse_entries(entries):
    """Check a change's entries.

    ``{"operation", "id"}`` for ``pause``, ``resume`` and ``delete``; a
    ``skip`` or ``unskip`` adds ``sends`` (its slot keys, ISO dates, each
    listed once); ``create`` holds
    ``values`` and ``edit`` an ``id`` and ``values`` (a JSON object, checked
    by ``change_rows``). One entry per report. Returns the entries with
    canonical ids. Anything else is an unknown request (400), which only a
    crafted or stale request makes.
    """
    if type(entries) is not list or not 1 <= len(entries) <= ENTRY_LIMIT:
        # No sources: a change document's entry, built by the page or the CLI.
        raise unexpected_fields()
    result, seen = [], set()
    for entry in entries:
        if type(entry) is not dict or entry.get("operation") not in OPERATIONS:
            # No sources: a change document's entry, built by the page or the CLI.
            raise unexpected_fields()
        operation = entry["operation"]
        if set(entry) != _keys(operation):
            # No sources: a change document's entry, built by the page or the CLI.
            raise unexpected_fields()
        parsed = {"operation": operation}
        try:
            if "id" in entry:
                parsed["id"] = str(UUID(str(entry["id"])))
            if "sends" in entry:
                parsed["sends"] = _slots(entry["sends"])
        except ValueError:
            # No sources: a change document's entry, built by the page or the CLI.
            raise unexpected_fields() from None
        if "values" in entry:
            if type(entry["values"]) is not dict:
                # No sources: a change document's entry, built by the page or the CLI.
                raise unexpected_fields()
            parsed["values"] = entry["values"]
        if parsed.get("id") in seen:
            # No sources: a change document's entry, built by the page or the CLI.
            raise unexpected_fields()
        seen.add(parsed.get("id"))
        result.append(parsed)
    return result


def _pause(values, now):
    """The report's pause intervals with one opened at ``now``."""
    ranges = list(values["paused_ranges"])
    if ranges and ranges[-1]["end"] is None:
        raise UserFacingError(ALREADY_PAUSED, fix=_("Reload the page to see it now."))
    if len(ranges) >= PAUSE_LIMIT:
        raise UserFacingError(TOO_MANY_PAUSES % {"limit": PAUSE_LIMIT})
    return ranges + [{"start": now.isoformat(), "end": None}]


def _resume(values, now):
    """The report's pause intervals with the open one closed at ``now``."""
    ranges = list(values["paused_ranges"])
    if not ranges or ranges[-1]["end"] is not None:
        raise UserFacingError(NOT_PAUSED, fix=_("Reload the page to see it now."))
    if now <= datetime.fromisoformat(ranges[-1]["start"]):
        # Only a clock that stepped back can close a pause before it began.
        raise stale_page()
    return ranges[:-1] + [ranges[-1] | {"end": now.isoformat()}]


def skip_send(identifier, values, campaign, slot, now, *, unskip=False):
    """The report send at ``slot`` if it may be skipped (or, with
    ``unskip``, its skip removed); else the refusal.

    A one-time report has no single send to skip (Delete removes it), and
    a slot that is no send of the report (a paused or missing day, or
    another report's) is an unknown request (400); a slot skipped already
    (for an unskip, one not skipped) is a stale page (409); a send that is
    past, or that has Production work, is refused (403), so a past skip is
    never removed. Returns the ``ScheduleSlot``.
    """
    if values["schedule"]["type"] != "repeat":
        # No sources: the send is checked against the saved report.
        raise unexpected_fields()
    if (slot in values["skip_dates"]) != unskip:
        raise stale_page()
    send = send_at(values, campaign, slot)
    if send is None:
        # No sources: the send is checked against the saved report.
        raise unexpected_fields()
    if send.due_at <= now:
        raise UserFacingDenied(UNSKIP_PASSED if unskip else SKIP_PASSED)
    if send_worked(identifier, slot):
        raise UserFacingDenied(UNSKIP_WORKED if unskip else SKIP_WORKED)
    return send


def send_worked(identifier, slot):
    """Whether the send has Production work: prepared, sending, sent or failed.

    A Testing rehearsal's occurrence is practice mail, so skipping the real
    send is still allowed.
    """
    return ScheduleOccurrence.objects.filter(
        definition_id=identifier, slot=slot, mode="production"
    ).exists()


# The values a ``create`` or ``edit`` entry may set (spec, "Report values");
# skips, pauses and the slot basis belong to the report's own actions.
EDITABLE = frozenset(
    {
        "name",
        "subject",
        "intro_html",
        "intro_text",
        "recipients",
        "schedule",
        "as_of",
        "blocks",
        "content_required",
    }
)
NEW_DEFAULTS = {
    "subject": "",
    "intro_html": "",
    "intro_text": "",
    "content_required": False,
    "skip_dates": [],
    "paused_ranges": [],
    "slot_basis": "send_date",
}
TOO_MANY = _(
    "This campaign already has %(limit)s report emails, the most it can have. "
    "Delete one first."
)


def report_sent(identifier):
    """Whether the report has made a scheduled send (a Production occurrence).

    Send now's manual occurrences do not count: they are no scheduled send.
    """
    return (
        ScheduleOccurrence.objects.filter(definition_id=identifier, mode="production")
        .exclude(slot__startswith="manual:")
        .exists()
    )


def report_of(given, saved, campaign_id):
    """The complete values a ``create`` or ``edit`` entry makes.

    ``given`` are the entry's values (only ``EDITABLE`` keys; any other is
    an unknown request), ``saved`` the report's saved values (None for a
    new report, which starts from ``NEW_DEFAULTS``). The name's spaces are
    collapsed.
    """
    if set(given) - EDITABLE:
        # No sources: a change document's values, not a form post.
        raise unexpected_fields()
    values = dict(saved or NEW_DEFAULTS) | given
    values |= {"kind": "report", "campaign_id": str(campaign_id)}
    if isinstance(values.get("name"), str):
        values["name"] = " ".join(values["name"].split())
    return values


# The values a new report must be given, by their names on the page.
REQUIRED = {
    "name": _("Name"),
    "recipients": _("Recipients"),
    "schedule": _("Schedule"),
    "as_of": _("As of"),
    "blocks": _("Blocks"),
}


# The value of a ``create`` or ``edit`` document each editor field reads
# into, for the command line's field problems (``values.<name>``).
DOCUMENT_FIELDS = {
    "recipient": "recipients",
    "block": "blocks",
    "days_shown": "blocks",
    "charts": "blocks",
    "sends": "schedule",
    "once_date": "schedule",
    "time": "schedule",
    "html": "intro_html",
}


def _refused(problems):
    """The refusal naming each ``(field, label, code, message)`` problem.

    A ``UserFacingError`` whose message leads each problem with the field's
    label on the page ("Recipients: ..."), and whose ``fields`` the command
    line's error document lists, one ``{"field", "code", "message"}`` each
    (``values.recipients``), as the schedule commands list theirs.
    """
    from parishkit.stewardship.admin_changes import FieldProblem

    texts = [
        f"{label}: {message}" if label else message
        for _f, label, _c, message in problems
    ]
    error = UserFacingError(" ".join(texts), fix=_("Nothing was changed."))
    error.fields = [
        FieldProblem(
            {
                "field": f"values.{field}" if field else "values",
                "code": code,
                "message": text,
            },
            logged=f"report-{field}" if field else "report",
        )
        for (field, _label, code, _message), text in zip(problems, texts, strict=True)
    ]
    return error


def _checked(values, *, saved, others, users, campaign, now, sent):
    """Refuse values the editor would refuse, naming every problem.

    The page's own checks (``report_email_editor.value_problems``), each
    problem named by its field (``_refused``), since the command line has no
    fields to show it at. A current user's address becomes that user's
    entry in ``values``.
    """
    from .report_email_editor import EditorForm, value_problems

    missing = [name for name in REQUIRED if name not in values]
    if missing:
        raise _refused(
            [
                (
                    name,
                    str(REQUIRED[name]),
                    "required",
                    str(_("This value is required.")),
                )
                for name in missing
            ]
        )
    problems = value_problems(
        values,
        saved=saved,
        others=others,
        users=users,
        campaign=campaign,
        now=now,
        sent=sent,
    )
    if problems:
        labels = EditorForm.base_fields
        raise _refused(
            [
                (
                    DOCUMENT_FIELDS.get(field, field),
                    str(labels[field].label)
                    if field in labels and labels[field].label
                    else "",
                    "invalid",
                    str(error.messages[0]),
                )
                for field, error in problems
            ]
        )


def _dropped_skips(saved, values, campaign, now):
    """The skips of sends still ahead that the change moves off the report.

    Each such skip is dropped, so it never applies again if the report
    later moves back; past skips are kept (a skipped past send has no
    fulfillment).
    """
    ahead = [
        key
        for key in saved["skip_dates"]
        if (send := send_at(saved, campaign, key)) is not None and send.due_at > now
    ]
    return [key for key in ahead if send_at(values, campaign, key) is None]


def send_at(values, campaign, key):
    """The send of ``values`` at slot ``key`` (skips set aside), or None."""
    page = plan(values | {"skip_dates": []}, campaign).page(
        through=datetime.max.replace(tzinfo=UTC),
        after=date.fromisoformat(key) - timedelta(days=1),
        limit=8,
    )
    return next((item for item in page.slots if item.key == key), None)


def change_rows(entries, records, campaign, now, *, campaign_id=None, users=()):
    """The configuration rows and reviewed send times of a change.

    ``records`` maps each report's id to its saved values; ``campaign`` is
    the applied campaign values; ``now`` the database's clock;
    ``campaign_id`` the campaign a ``create`` adds to and ``users`` the
    Admin portal users now (``portal_users``). Returns ``(patch,
    due, changes)``: ``due`` the send instants the review signs (a skipped
    send, a new or moved one-time send), and ``changes`` one description
    per entry for the review and the command line. A ``create`` or
    ``edit`` change carries its ``before`` and ``after`` values, the skips
    it drops and its first send after the campaign's last day
    (``after_campaign``, decision 9), or None.
    """
    patch, due, changes = [], [], []
    stamp = now.astimezone(UTC)
    creates = sum(entry["operation"] == "create" for entry in entries)
    if creates and len(records) + creates > REPORT_LIMIT:
        raise UserFacingDenied(TOO_MANY % {"limit": REPORT_LIMIT})
    for entry in entries:
        operation = entry["operation"]
        if operation in ("create", "edit"):
            changes.append(
                _write(entry, records, campaign, now, campaign_id, users, patch, due)
            )
            continue
        identifier = entry["id"]
        values = records.get(identifier)
        if values is None:
            # No sources: the fields were allowed; the id is no report's.
            raise unexpected_fields()
        change = {"id": identifier, "operation": operation, "name": values["name"]}
        if operation == "delete":
            patch.append(
                {"operation": "remove", "section": "schedules", "id": identifier}
            )
        else:
            if operation == "pause":
                update = {"paused_ranges": _pause(values, stamp)}
            elif operation == "resume":
                update = {"paused_ranges": _resume(values, stamp)}
                # The first send it makes is signed, so Apply is refused once
                # that send's time has passed: applied later, it would send
                # late (#1464).
                first = plan(values | update, campaign).next_slot(after=now)
                if first is not None:
                    due.append(first.due_at)
                    change |= {"next_send": first.due_at}
            else:
                # Each send is checked on its own and its time signed, so
                # Apply refuses once any of them has passed (#1464).
                unskip = operation == "unskip"
                sends = [
                    skip_send(identifier, values, campaign, key, now, unskip=unskip)
                    for key in entry["sends"]
                ]
                keys = {send.key for send in sends}
                kept = set(values["skip_dates"])
                update = {"skip_dates": sorted(kept - keys if unskip else kept | keys)}
                due.extend(send.due_at for send in sends)
                change |= {
                    "sends": [
                        {"send": send.key, "due_at": send.due_at}
                        for send in sorted(sends, key=lambda item: item.due_at)
                    ]
                }
            patch.append(
                {
                    "operation": "update",
                    "section": "schedules",
                    "id": identifier,
                    "values": update,
                }
            )
        changes.append(change)
    return patch, due, changes


def _write(entry, records, campaign, now, campaign_id, users, patch, due):
    """One ``create`` or ``edit`` entry: its row, signed time and description."""
    from .report_email_editor import (
        after_campaign,
        chart_choice,
        one_time_due,
        stored_charts,
        with_charts,
    )

    identifier = entry.get("id")
    saved = records.get(identifier) if identifier else None
    if entry["operation"] == "edit" and saved is None:
        # No sources: the entry names no saved report.
        raise unexpected_fields()
    values = report_of(entry["values"], saved, campaign_id)
    others = [found for key, found in records.items() if key != identifier]
    _checked(
        values,
        saved=saved,
        others=others,
        users=users,
        campaign=campaign,
        now=now,
        sent=bool(identifier) and report_sent(identifier),
    )
    # The "which charts" choice is stored as the editor stores it: every
    # chart the campaign offers is no choice (#1956).
    choice = chart_choice(values)
    stored = stored_charts(choice, saved, campaign)
    if stored != choice:
        values = with_charts(values, stored)
    dropped = []
    if saved is not None:
        dropped = _dropped_skips(saved, values, campaign, now)
        values["skip_dates"] = [
            key for key in values["skip_dates"] if key not in dropped
        ]
        if values == saved:
            raise UserFacingError(
                _("Nothing has changed."), fix=_("Change a field, then review it.")
            )
    schedule = values["schedule"]
    if schedule["type"] == "once" and (saved is None or saved["schedule"] != schedule):
        # Apply refuses once the reviewed send time has passed (#1464).
        due.append(one_time_due(schedule, campaign["timezone"]))
    if saved is None:
        identifier = str(uuid4())
        patch.append(
            {
                "operation": "add",
                "section": "schedules",
                "id": identifier,
                "values": values,
            }
        )
    else:
        patch.append(
            {
                "operation": "update",
                "section": "schedules",
                "id": identifier,
                "values": {
                    name: value
                    for name, value in values.items()
                    if value != saved.get(name)
                },
            }
        )
    late = after_campaign(values, campaign, now)
    return {
        "id": identifier,
        "operation": entry["operation"],
        "name": values["name"],
        "before": saved,
        "after": values,
        "dropped_skips": dropped,
        "after_campaign": late.due_at if late is not None else None,
    }


def build_review(service, actor, state, campaign, entries, *, base_digest):
    """Build and sign one report email change, or refuse it.

    ``state`` is ``campaign_views._state`` read in the caller's work
    transaction, ``campaign`` the campaign it names and ``entries`` the
    parsed entries (``parse_entries``). A ``base_digest`` other than the
    applied configuration's is the stale page's refusal. Returns ``{"changes",
    "blocking", "preview", "after_campaign"}``: ``blocking`` counts the
    affected reports' work in progress or uncertain, ``preview`` is the
    signed change, None while anything blocks it, and ``after_campaign``
    whether a report would send after the campaign's last day, which Apply
    then needs confirmed (decision 9); the signed change records that, so
    the page and ``report-email confirm`` ask the same question.
    """
    from .report_email_editor import portal_users

    refuse_archived(campaign)
    configuration = state[0]
    digest = configuration.active_configuration.digest
    if base_digest != digest:
        raise stale_page()
    records = {
        row["id"]: row["values"] for row in report_records(configuration, campaign.pk)
    }
    # A send of a changed report still being prepared, a Send now queued
    # included, refuses the change, as the page's row does: the work
    # summary counts queued work as cancellable, and a change would leave
    # that send on a revision no longer current.
    for entry in entries:
        if entry.get("id") and open_send(entry["id"], configuration.mode):
            raise blocked()
    now = database_now()
    patch, due, changes = change_rows(
        entries,
        records,
        campaign.active_configuration.values,
        now,
        campaign_id=campaign.pk,
        users=portal_users(configuration),
    )
    base = service.store.active()
    if base is None or base.digest != digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        # The candidate is checked whole, as it will be recorded.
        build_candidate(base, patch, candidate_id=uuid4())
    except (ConfigError, ValueError):
        raise UserFacingError(
            DOES_NOT_FIT, fix=_("Reload the page and try again.")
        ) from None
    summary = work_summary(campaign.pk)
    blocking = sum(
        summary.get(change["id"], {}).get("blocking", 0) for change in changes
    )
    late = any(change.get("after_campaign") for change in changes)
    return {
        "changes": changes,
        "blocking": blocking,
        "after_campaign": late,
        "preview": None
        if blocking
        else sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=preview_salt(campaign.pk),
            snapshot=work_fingerprint(state, campaign.pk, summary),
            due=sorted(moment.isoformat() for moment in due),
            extra={"after_campaign": True} if late else None,
        ),
    }


def open_send(identifier, mode):
    """Whether a send of the report is being prepared, or its email delivered."""
    from .report_email_models import ReportEmailPreparation, ReportEmailRecipient

    preparing = (
        ReportEmailPreparation.objects.filter(definition_id=identifier, mode=mode)
        .exclude(phase__in=("complete", "cancelled"))
        .exists()
    )
    return preparing or (
        ReportEmailRecipient.objects.filter(
            snapshot__preparation__definition_id=identifier,
            snapshot__preparation__mode=mode,
            outbox_id__isnull=False,
        )
        .exclude(outbox__state__in=("delivered", "permanent_failure", "cancelled"))
        .exists()
    )


AFTER_CAMPAIGN = _(
    "This report email sends after the campaign ends. Tick Send after the "
    "campaign ends to apply it."
)


def needs_after_campaign(token, campaign_id):
    """Whether a signed change needs the after-campaign confirmation.

    Read from the signed preview itself, so the question cannot be left out
    of a request; a token that does not verify is left to the confirmation,
    which refuses it.
    """
    from django.core import signing

    try:
        intent = signing.loads(token, salt=preview_salt(campaign_id))
    except signing.BadSignature:
        return False
    return bool((intent.get("extra") or {}).get("after_campaign"))


def blocked():
    """The refusal of a change that work in progress or uncertain blocks."""
    return UserFacingDenied(BLOCKED, fix=BLOCKED_FIX)
