"""The ``report-email`` commands of the Admin command line (#1786, item 5).

Each reaches one action of Mail › Report emails through the page's own
functions, admission, checks and audit (report-emails spec, "Command
line"; admin-automation, "Rules for new Admin actions"):

- ``report-email list`` is the page's table (``report_email_list.listing``):
  each report's id, name, schedule in plain words, next and last send and
  status, and the deleted reports that sent something. Any session; Staff
  get no recipients and no configuration version, as the page shows them.
- ``report-email show REPORT`` is one report's saved values, its skips,
  pauses and next five sends, and the configuration version a change
  document names. Outside addresses are shown to Administrators only.
- ``report-email sends REPORT`` is the report's past sends, ids and dates
  only, as the page lists them (a deleted report's too).
- ``report-email copy SEND`` is a saved copy, read from its snapshot: each
  block's figures and, for the open requests block, each request's id and
  current state, never a Family's text; it records the page's
  ``report_email_viewed``. A daily or weekly digest saved before the
  conversion is read through its own page's functions (``admin_digests``).
- ``report-email render REPORT`` is the row's Preview (#2035): the
  report drawn from current data, its subject, text part and recipients,
  never its HTML; Administrators only; it records the page's
  ``report_email_previewed``.
- ``report-email send-test REPORT`` is the row's Send a test to me (#2036),
  keyed by its request key: the report drawn now, sent only to the
  Administrator (the Testing recipient in Testing); it records the page's
  ``report_email_test_sent``.
- ``report-email send-now REPORT`` is the row's Send now, keyed by its
  request key, with the dialog's words asked at the prompt (or ``--yes``).
- ``report-email preview`` and ``confirm`` are the row's Pause, Resume and
  Delete and the editor's Skip and Send selected sends (``skip`` and
  ``unskip``, ``report_email_changes``): the
  change is built, validated and signed as the page builds it, and the
  token is confirmed under the work lock as every schedule change is.

The reads admit passively as the page does and record no event, except
``copy`` and ``render``, which record their pages' own.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_reads import NotAvailable, ReadModel, _held, _recheck
from .admin_report_reads import _admit


@dataclass(frozen=True)
class ReportEmailList(ReadModel):
    """The Report emails table, as the page shows it to the reader.

    ``reports`` are the campaign's report emails in the page's order, each
    with its ``id``, ``name``, ``schedule`` (plain words, times as UTC
    instants), ``next_send``, ``last_send`` (its ``send_id``, ``made_at``
    and ``result``; None before the first), ``status`` and
    ``paused_since``, and for an Administrator its ``recipients`` in short
    and ``recipient_entries``. ``deleted`` are the deleted reports that
    sent something (``id``, ``name`` and ``sends``). ``version`` is the
    applied configuration a change document names (None for Staff).
    """

    campaign_id: UUID | None
    version: str | None
    reports: list
    deleted: list


@dataclass(frozen=True)
class ReportEmailShow(ReadModel):
    """One report's values, as its Edit page holds them.

    ``recipients`` lists each entry by kind (``user`` with its name,
    ``group`` or ``address``); it is None for Staff. ``next_sends`` are its
    next five sends (``send``, the slot key a skip names, and ``due_at``).
    """

    id: UUID
    campaign_id: UUID
    version: str | None
    name: str
    subject: str
    recipients: list | None
    schedule: dict
    schedule_text: str
    as_of: str
    blocks: list
    content_required: bool
    skip_dates: list
    paused_ranges: list
    status: str
    next_sends: list


@dataclass(frozen=True)
class ReportEmailSends(ReadModel):
    """One report's past sends, newest first: ids, dates and results only."""

    id: UUID
    name: str
    sends: list


@dataclass(frozen=True)
class ReportEmailCopy(ReadModel):
    """A report email send's saved copy, from its snapshot.

    ``blocks`` holds each block in the send's order: its ``block`` key,
    ``version`` and ``data``, the figures the email showed; the open
    requests block lists each request's ``item_id``, ``submitted_at`` and
    current ``disposition``, never its Family or text. ``as_of_line``,
    ``how``, ``covers`` (a recovery send's days, else "") and
    ``recipients`` (``users``, ``groups``, ``outside`` and
    ``outside_count``; ``outside`` is None for Staff) are what the page
    states about the send (``report_email_copy.copy_facts``, #1946).
    """

    send_id: UUID
    report_id: UUID
    campaign_id: UUID
    name: str
    how: str
    made_at: str
    as_of: str | None
    as_of_line: str
    covers: str
    recipients: dict
    blocks: list


@dataclass(frozen=True)
class ReportEmailRender(ReadModel):
    """A report email drawn from current data, as its Preview shows it (#2035).

    ``subject`` and ``text`` are the email's subject and plain-text part
    (never its HTML); ``as_of`` the report's as-of choice and ``as_of_at``
    the moment every figure is counted at; ``updating`` says the figures
    are not those a send would build now. ``recipients`` are who it would
    go to now, by kind (``users``, ``groups``, ``outside`` and
    ``outside_count``), as the dialog names them. ``mode`` is Production or
    Testing, where every message goes to the Testing recipient.
    """

    id: UUID
    campaign_id: UUID
    name: str
    subject: str
    text: str
    as_of: str
    as_of_at: str
    updating: bool
    mode: str
    recipients: dict


@dataclass(frozen=True)
class ReportEmailSendTest(ReadModel):
    """The Send a test to me a ``report-email send-test`` made or found (#2036).

    ``id`` is the test's request key; ``created`` is false for a repeated
    key, which returns the original test (``subject`` and ``updating`` are
    then None: the email is not drawn again). ``subject`` is the test's
    labeled subject; ``updating`` says its figures were not the newest.
    ``recipient`` is ``self`` (the Administrator's own address) or
    ``testing`` (the Testing recipient). Follow ``task``, which makes the
    test's message, with ``task show --watch``; the message is then on
    ``delivery list --purpose admin_report_test``.
    """

    id: UUID
    report_id: UUID
    created: bool
    subject: str | None
    updating: bool | None
    recipient: str
    task: dict


@dataclass(frozen=True)
class ReportEmailSendNow(ReadModel):
    """The Send now a ``report-email send-now`` made or found.

    ``created`` is false for a repeated key, which returns the original
    request's task unchanged. Follow ``task`` with ``task show --watch``;
    ``report-email sends`` lists the send once it is made.
    """

    created: bool
    request_key: UUID
    report_id: UUID
    task: dict


@dataclass(frozen=True)
class ReportEmailPreview(ReadModel):
    """A reviewed change of report emails and the token that confirms it.

    ``changes`` names each report changed (``id``, ``operation``, ``name``
    and, for a skip or unskip, its ``sends``, each a ``send`` slot key and
    its ``due_at``, earliest first; a ``create`` or
    ``edit`` its ``before`` and ``after`` values, the ``dropped_skips`` and
    its first send after the campaign's last day, ``after_campaign``).
    ``after_campaign`` says the change makes a report send after the
    campaign ends, which ``confirm`` asks about (decision 9). While ``blocking``
    is above zero (a send in progress or uncertain), as on the page, there
    is no ``preview``; otherwise ``preview.token`` is valid for fifteen
    minutes.
    """

    campaign_id: UUID
    version: str
    changes: list
    blocking: int
    after_campaign: bool
    preview: dict | None


@dataclass(frozen=True)
class ReportEmailConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed.
    """

    created: bool
    request: dict


def _report_principal(caller, store, *, read_only=False):
    """The page's admission: Administrators and Staff."""
    from .reports.export_views import _principal

    return _principal(caller, store, read_only=read_only)


def _system(campaign_id):
    """The configuration row and the campaign a command names, or refuse.

    ``campaign_id`` None names the current campaign; none at all, or an
    unknown one, is ``not_available``. Only the current campaign is listed
    by the page, so another one is ``not_available`` too.
    """
    from .accounts.runtime_models import SystemConfiguration

    system = SystemConfiguration.objects.select_related(
        "active_configuration", "current_campaign__active_configuration"
    ).get()
    campaign = system.current_campaign
    if campaign is None or (campaign_id is not None and campaign.pk != campaign_id):
        raise NotAvailable("There is no such current campaign.")
    return system, campaign


def _administrator(actor):
    """Whether the reader holds the Administrator's capability."""
    from .accounts.policy import Capability, allows

    return allows(actor, Capability.CONFIGURE)


def _last(send):
    """A past send's summary for ``list``: ids and dates only."""
    if send is None:
        return None
    return {
        "send_id": send["id"],
        "kind": send["kind"],
        "made_at": send["made_at"],
        "result": send["result"],
    }


def _send(send):
    """A past send for ``sends`` and ``list``'s deleted reports."""
    return {
        "send_id": send["id"],
        "kind": send["kind"],
        "made_at": send["made_at"],
        "how": send["how"],
        "as_of": send["as_of"],
        "recipients": send["recipients"],
        "result": send["result"],
        "count": send["count"],
    }


def read_list(caller, service, *, campaign_id):
    """``report-email list``: the page's table, as the page reads it."""
    from django.utils import timezone

    from .reports.report_email_list import listing, schedule_text

    def step():
        """Admit, read the page's listing and recheck."""
        actor = _admit(caller, service, _report_principal)
        administrator = _administrator(actor)
        system, campaign = _system(campaign_id)
        view = listing(
            system, campaign, system.mode, timezone.now(), administrator=administrator
        )
        _recheck(caller, service.store, actor)
        return ReportEmailList(
            campaign_id=campaign.pk,
            version=system.active_configuration.digest if administrator else None,
            reports=[
                {
                    "id": row["id"],
                    "name": row["name"],
                    "schedule": schedule_text(row["schedule"]),
                    "next_send": row["next"].due_at if row["next"] else None,
                    "last_send": _last(row["last"]),
                    "status": row["status"],
                    "paused_since": row["paused_since"],
                }
                | (
                    {
                        "recipients": str(row["recipients"]),
                        "recipient_entries": row["recipient_count"],
                    }
                    if administrator
                    else {}
                )
                for row in view["rows"]
            ],
            deleted=[
                {
                    "id": row["id"],
                    "name": row["name"],
                    "sends": [_send(send) for send in row["sends"]],
                }
                for row in view["deleted"]
            ],
        )

    return _held(step)


def _record(system, campaign_id, report_id):
    """One saved report's ``{"id", "values"}``, or ``not_available``."""
    from .reports.report_email_list import report_records

    found = next(
        (
            row
            for row in report_records(system, campaign_id)
            if row["id"] == str(report_id)
        ),
        None,
    )
    if found is None:
        raise NotAvailable("No such report email.")
    return found


def _recipients(values, system):
    """Each recipient entry by kind, a user with its name (Administrators only)."""
    from .reports.report_email_list import user_names

    names = user_names(system)
    result = []
    for entry in values["recipients"]:
        ((kind, value),) = entry.items()
        if kind == "user":
            result.append({"kind": "user", "user": value, "name": names.get(value)})
        else:
            result.append({"kind": kind, kind: value})
    return result


def read_show(caller, service, report_id, *, campaign_id):
    """``report-email show REPORT``: one report's values and next sends."""
    from django.utils import timezone

    from .reports import report_email_list as rows

    def step():
        """Admit, read the report's saved values and its plan, and recheck."""
        actor = _admit(caller, service, _report_principal)
        administrator = _administrator(actor)
        system, campaign = _system(campaign_id)
        values = _record(system, campaign.pk, report_id)["values"]
        dates = campaign.active_configuration.values
        now = timezone.now()
        evaluator, upcoming, after = rows.plan(values, dates), [], now
        while len(upcoming) < 5:
            # The next send after the last one found: skipped and paused
            # sends are left out, as the scheduler leaves them out.
            slot = evaluator.next_slot(after=after)
            if slot is None:
                break
            upcoming.append(slot)
            after = slot.due_at
        sends = rows.report_sends(campaign.pk, system.mode, [str(report_id)])
        upcoming_one = upcoming[0] if upcoming else None
        _recheck(caller, service.store, actor)
        return ReportEmailShow(
            id=report_id,
            campaign_id=campaign.pk,
            version=system.active_configuration.digest if administrator else None,
            name=values["name"],
            subject=values["subject"],
            recipients=_recipients(values, system) if administrator else None,
            schedule=values["schedule"],
            schedule_text=rows.schedule_text(rows.schedule_words(values, dates, now)),
            as_of=values["as_of"],
            blocks=values["blocks"],
            content_required=values["content_required"],
            skip_dates=values["skip_dates"],
            paused_ranges=values["paused_ranges"],
            status=rows.status(values, sends.get(str(report_id), []), upcoming_one),
            next_sends=[{"send": slot.key, "due_at": slot.due_at} for slot in upcoming],
        )

    return _held(step)


def read_sends(caller, service, report_id, *, campaign_id):
    """``report-email sends REPORT``: its past sends, a deleted report's too."""
    from .reports import report_email_list as rows

    def step():
        """Admit, read the report's past sends and recheck."""
        actor = _admit(caller, service, _report_principal)
        system, campaign = _system(campaign_id)
        sends = rows.report_sends(campaign.pk, system.mode, [str(report_id)]).get(
            str(report_id), []
        )
        saved = next(
            (
                row
                for row in rows.report_records(system, campaign.pk)
                if row["id"] == str(report_id)
            ),
            None,
        )
        if saved is None and not sends:
            raise NotAvailable("No such report email.")
        name = (
            saved["values"]["name"]
            if saved
            else next((send["name"] for send in sends if send["name"]), "")
        )
        _recheck(caller, service.store, actor)
        return ReportEmailSends(
            id=report_id, name=name, sends=[_send(send) for send in sends]
        )

    return _held(step)


def _block_copy(entry):
    """One block of a saved copy for ``copy``: figures, never Family text.

    The open requests block keeps each request's id and submitted time
    (its current disposition is added by ``read_copy``); Compared with past
    campaigns is projected as a saved daily digest is (``admin_digests``); every other
    block's data holds only aggregate figures and is kept as captured.
    """
    from .admin_digests import _section

    data = entry.get("data")
    if entry["block"] == "additional_information" and data:
        data = {
            "items": [
                {"item_id": item[0], "submitted_at": item[3]} for item in data["items"]
            ]
        }
    elif entry["block"] == "compared_with_past_campaigns" and data:
        data = _section(data)
    return {"block": entry["block"], "version": entry.get("version"), "data": data}


def _report_copy(caller, service, actor, send_id):
    """The saved copy of a report email send, or None when it is not one."""
    from .campaigns.models import ScheduleRevision
    from .reports.export_services import admit_campaign
    from .reports.report_email_copy import copy_facts
    from .reports.report_email_copy_views import _pinned, readable
    from .reports.report_email_copy_views import _record as record_view
    from .reports.report_email_models import (
        ReportEmailSnapshot,
    )
    from .responses.models import AdditionalInformationItem
    from .web import dates

    # Only the columns the web login may read: the copy page's and the
    # bindings (``report_email_grants``).
    snapshot = (
        ReportEmailSnapshot.objects.only(
            "id",
            "preparation_id",
            "campaign_id",
            "configuration_id",
            "created_at",
            "blocks",
            "values",
            "recipients",
        )
        .filter(preparation_id=send_id)
        .first()
    )
    if snapshot is None:
        return None
    if not readable(snapshot.blocks, actor.roles):
        raise PermissionError("This saved copy holds a block the reader may not read.")
    admit_campaign(snapshot.campaign_id, mutating=False)
    blocks = [_block_copy(entry) for entry in snapshot.blocks]
    listed = [
        item["item_id"]
        for block in blocks
        if block["block"] == "additional_information" and block["data"]
        for item in block["data"]["items"]
    ]
    states = dict(
        AdditionalInformationItem.objects.filter(pk__in=listed).values_list(
            "pk", "disposition"
        )
    )
    for block in blocks:
        if block["block"] == "additional_information" and block["data"]:
            for item in block["data"]["items"]:
                item["disposition"] = states.get(UUID(item["item_id"]))
    preparation = _pinned(snapshot)
    # The page's own statements about the send, in the email's date format.
    with dates.using(snapshot.configuration.parish.date_format):
        facts = copy_facts(snapshot, preparation, administrator=_administrator(actor))
        as_of_line = facts.as_of_line
    _recheck(caller, service.store, actor)
    record_view(actor, send_id, snapshot.campaign_id)
    return ReportEmailCopy(
        send_id=send_id,
        report_id=preparation.definition_id,
        campaign_id=snapshot.campaign_id,
        name=ScheduleRevision.objects.filter(pk=preparation.revision_id)
        .values_list("values__name", flat=True)
        .first()
        or "",
        how=facts.how,
        made_at=snapshot.created_at,
        as_of=preparation.as_of,
        as_of_line=as_of_line,
        covers=facts.covers,
        recipients=facts.recipients,
        blocks=blocks,
    )


def read_copy(caller, service, send_id, *, page):
    """``report-email copy SEND``: a saved copy, as its page reads it.

    A report email send's snapshot, or a daily or weekly digest saved
    before the conversion, read by its own page's functions
    (``admin_digests.read_digest``; ``page`` pages a weekly report's items). An
    id that is neither is ``not_available``.
    """
    from .admin_digests import read_digest
    from .reports.digest_models import DailyDigestSnapshot
    from .reports.weekly_models import WeeklyDigestSnapshot

    def step():
        """Admit and read a report email send's copy, if the id names one."""
        actor = _admit(caller, service, _report_principal)
        found = _report_copy(caller, service, actor, send_id)
        if found is None:
            _recheck(caller, service.store, actor)
        return found

    model = _held(step)
    if model is not None:
        return model
    if DailyDigestSnapshot.objects.filter(pk=send_id).exists():
        return read_digest(caller, service, send_id, kind="daily", page=None)
    if WeeklyDigestSnapshot.objects.filter(pk=send_id).exists():
        return read_digest(caller, service, send_id, kind="weekly", page=page)
    raise NotAvailable("No such saved copy.")


def read_render(caller, service, report_id, *, campaign_id):
    """``report-email render REPORT``: the row's Preview, as its dialog draws it.

    Administrators only, any session scope: the report compiled from
    current data by the page's own function, in one read snapshot, then the
    page's audit event (``report_email_previewed``). No ParishSoft data yet,
    or a chart that cannot be drawn, is ``not_available``.
    """
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction
    from .reports.export_services import admit_campaign
    from .reports.report_email_copy import recipients_by_kind
    from .reports.report_email_list import version_user_names
    from .reports.report_email_preview_views import _audit, draw

    def step():
        """Admit an Administrator, draw the report and recheck."""
        actor = _admit(caller, service, _report_principal)
        if not _administrator(actor):
            raise PermissionError("Only Administrators preview report emails.")
        with read_transaction():
            system, campaign = _system(campaign_id)
            values = _record(system, campaign.pk, report_id)["values"]
            admit_campaign(campaign.pk, mutating=False)
            try:
                preview = draw(values, system, campaign)
            except LookupError as error:
                raise NotAvailable(str(error)) from None
            names = version_user_names(system.active_configuration)
            parish_id = system.active_configuration.parish.pk
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        _audit(actor, report_id, campaign.pk, parish_id)
        return ReportEmailRender(
            id=report_id,
            campaign_id=campaign.pk,
            name=values["name"],
            subject=preview.subject,
            text=preview.text,
            as_of=preview.as_of,
            as_of_at=preview.as_of_at.isoformat(),
            updating=preview.updating,
            mode=preview.mode,
            recipients=recipients_by_kind(
                preview.recipients, names, administrator=True
            ),
        )

    return _held(step)


def send_test(caller, service, report_id, *, campaign_id, request_key, context):
    """``report-email send-test REPORT``: the row's Send a test to me (#2036).

    Admits as the page's post does (recording activity, so a full-scope
    session), for an Administrator and the current campaign's report, then
    draws and queues the test through the page's own function, in the
    page's command scope. A pending test of the report is ``stale_version``;
    no ParishSoft data, busy figures, a chart that cannot be drawn,
    Production mail paused or an archived campaign is ``not_available``.
    ``context["committed"]`` is set once the test may have committed.
    """
    from .admin_operations import _admit as admit_form
    from .admin_operations import _ended_or_raise, task_retry_model
    from .jobs.task_retries import command_scope
    from .reports.report_email_changes import refuse_archived
    from .reports.report_email_test import request_test, task_of
    from .web.refusals import UserFacingUnavailable

    actor = admit_form(caller, service)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """Draw the report and queue its test inside the page's command scope."""
        written.clear()
        if not _administrator(actor):
            raise PermissionError("Only Administrators send report email tests.")
        system, campaign = _system(campaign_id)
        try:
            refuse_archived(campaign)
        except PermissionError as error:
            raise NotAvailable(str(error)) from None
        values = _record(system, campaign.pk, report_id)["values"]
        try:
            row, preview, created = request_test(
                actor.identity,
                values,
                definition_id=report_id,
                request_id=request_key,
                scope=command_scope(caller, service, actor),
            )
        except (LookupError, UserFacingUnavailable) as error:
            raise NotAvailable(str(error)) from None
        written.append(True)
        retry = task_retry_model(task_of(row), created=created, request_key=request_key)
        return ReportEmailSendTest(
            id=row.pk,
            report_id=report_id,
            created=created,
            subject=None if preview is None else "[TEST] " + preview.subject,
            updating=None if preview is None else preview.updating,
            recipient="testing" if row.mode == "testing" else "self",
            task=retry.task,
        )

    try:
        model = _held(step)
    except DatabaseError:
        # Only once the test was recorded may it have committed (exit 6).
        if written:
            context["committed"] = True
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model


def send_now_lines(caller, service, report_id, *, campaign_id):
    """The Send now dialog's words for the prompt, and the revision they show.

    Admits as the page's read does; ``not_available`` for a report that is
    not one of the campaign's. Returns ``(lines, revision)``: ``send_now``
    is bound to that revision, so a report changed between the prompt and
    the answer is refused as stale, as the page's dialog is.
    """
    from django.utils import timezone

    from .reports.report_email_list import listing

    def step():
        """Read the row whose dialog the prompt repeats."""
        actor = _admit(caller, service, _report_principal)
        system, campaign = _system(campaign_id)
        rows = listing(
            system, campaign, system.mode, timezone.now(), administrator=True
        )["rows"]
        row = next((row for row in rows if row["id"] == str(report_id)), None)
        if row is None:
            raise NotAvailable("No such report email.")
        _recheck(caller, service.store, actor)
        if not row["send_with"]:
            # A report with no current revision has nothing to send.
            raise NotAvailable("No such report email.")
        revision = UUID(row["send_with"].removeprefix("revision="))
        return (f"Send {row['name']} now.", row["send_body"]), revision

    return _held(step)


def send_now(
    caller, service, report_id, *, campaign_id, request_key, context, revision=None
):
    """``report-email send-now REPORT``: the row's Send now, as its post does.

    Admits as the page's post does (recording activity, so a full-scope
    session), for the current campaign's report, bound to its current
    revision and the applied configuration, in the page's command scope.
    Records ``admin_cmd_report_email_send_now`` only when it created the
    request. The database guard's refusal (the report changed, or another
    send of it is open) is ``stale_version``; a key bound to another
    request is ``invalid``. ``revision`` is the revision the prompt showed
    (``send_now_lines``); a report changed since then is ``stale_version``.
    ``context["committed"]`` is set once the request may have committed
    (exit 6 then names the key).
    """
    from .admin_operations import _admit as admit_form
    from .admin_operations import (
        _ended_or_raise,
        _raise_stale_on_conflict,
        task_retry_model,
    )
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .campaigns.schedule_models import ScheduleDefinition
    from .jobs.task_retries import command_scope
    from .observability import _guard_refusal
    from .reports.report_email_changes import refuse_archived
    from .reports.report_email_manual import request_send_now
    from .reports.report_email_models import ReportEmailManualRequest

    actor = admit_form(caller, service)
    context["request_id"] = str(request_key)
    written = []

    def step():
        """Request the send inside the page's command scope."""
        written.clear()
        system, campaign = _system(campaign_id)
        refuse_archived(campaign)
        _record(system, campaign.pk, report_id)
        current = (
            ScheduleDefinition.objects.filter(pk=report_id)
            .values_list("current_revision_id", flat=True)
            .first()
        )
        if current is None:
            raise NotAvailable("No such report email.")
        with command_scope(caller, service, actor):
            repeat = ReportEmailManualRequest.objects.filter(pk=request_key).exists()
            task = request_send_now(
                service.store,
                actor.identity,
                campaign.pk,
                report_id,
                # The revision the prompt showed; the guard refuses it once
                # the report has changed.
                revision or current,
                command_id=request_key,
                configuration_id=system.active_configuration_id,
            )
            if not repeat:
                record_action(
                    Action.ADMIN_CMD_REPORT_EMAIL_SEND_NOW,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
                written.append(True)
        retry = task_retry_model(task, created=not repeat, request_key=request_key)
        return ReportEmailSendNow(
            created=retry.created,
            request_key=request_key,
            report_id=report_id,
            task=retry.task,
        )

    try:
        model = _held(step)
    except DatabaseError as error:
        if written and not _guard_refusal(error):
            context["committed"] = True
        # The page's 409: the report changed, or another send of it is open.
        _raise_stale_on_conflict(error)
        raise
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return model


# The longest change document ``report-email preview`` reads, and token.
CHANGES_LIMIT = 64_000
TOKEN_LIMIT = 256_000


def parse_document(text):
    """The entries of a ``report-email preview`` change document.

    ``{"changes": [ENTRY, ...]}``, each entry as ``report_email_changes.
    parse_entries`` takes them. Anything else is ``invalid``.
    """
    import json

    from .reports.report_email_changes import parse_entries

    try:
        document = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if type(document) is not dict or set(document) != {"changes"}:
        raise ValueError('The change document must be {"changes": [...]}.')
    return parse_entries(document["changes"])


def preview(caller, service, campaign_id, *, expected_version, changes):
    """``report-email preview``: review New, Edit, Pause, Resume, Delete or Skip.

    Admits as the page's post does, then, in one work transaction, finds
    the campaign as the page does (one that is not current, or that work
    holds, is ``stale_version``) and builds the page's change. A base other
    than ``expected_version`` is ``stale_version``. Changes nothing.
    """
    from .accounts.policy import Capability
    from .admin_changes import _admit_change
    from .admin_changes import _ended_or_raise as ended_or_raise
    from .campaigns.work_locks import work_transaction
    from .reports.report_email_changes import build_review, change_state

    actor = _admit_change(caller, service)
    entries = parse_document(changes)

    def step():
        """Build the change in one work transaction, as the page's post does."""
        with work_transaction():
            system, campaign = _system(campaign_id)
            try:
                state, campaign = change_state(service, campaign.pk)
            except LookupError:
                raise NotAvailable("No such campaign.") from None
            reviewed = build_review(
                service,
                actor,
                state,
                campaign,
                entries,
                base_digest=expected_version,
            )
            return ReportEmailPreview(
                campaign_id=campaign.pk,
                version=expected_version,
                changes=reviewed["changes"],
                blocking=reviewed["blocking"],
                after_campaign=reviewed["after_campaign"],
                preview=None
                if reviewed["preview"] is None
                else {"token": reviewed["preview"]},
            )

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm(caller, service, campaign_id, *, token, context, ask=None):
    """``report-email confirm``: apply a reviewed change of report emails.

    As ``schedule confirm`` (``admin_changes.confirm_schedule``), with this
    page's salt and recheck: an expired or out-of-date token is
    ``stale_version``, an altered one or another campaign's ``invalid``,
    another Administrator's ``denied``. A change that makes a report send
    after the campaign ends is asked about first (``ask(lines)``, the
    prompt or ``--yes``), as the page's tick asks. Records
    ``admin_cmd_report_email_confirm`` with the request it created.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent
    from .accounts.configuration_requests import policy_operation_id
    from .admin_changes import _admit_change
    from .admin_changes import _ended_or_raise as ended_or_raise
    from .admin_reads import config_request
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal
    from .reports.report_email_changes import confirm_scope, preview_salt

    actor = _admit_change(caller, service)
    try:
        target = _held(lambda: _system(campaign_id)[1].pk)
    except NotAvailable as error:
        ended_or_raise(caller, service, actor, error)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=preview_salt(target))["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    from .reports.report_email_changes import needs_after_campaign

    if ask is not None and needs_after_campaign(token, target):
        ask(
            (
                "This change makes a report email send after the campaign ends.",
                "Send after the campaign ends.",
            )
        )
    created = []

    def attach(request, extra):
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_REPORT_EMAIL_CONFIRM,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=preview_salt(target),
            link=None,
            current_scope=lambda service: confirm_scope(service, target),
            attach=attach,
        )
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ReportEmailConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
