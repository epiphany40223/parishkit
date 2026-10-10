"""Configuration change commands of the Admin automation command line (ADM-11 PR 4).

``schedule preview`` and ``schedule confirm`` change mail schedules and the
draft campaign dates as the Dates and mail schedules page does, through the same
functions (``accounts.schedule_changes``, ``admin_editing.confirm_intent``),
and ``config request cancel`` cancels a live end-date change that has not
applied (#944):

- ``schedule preview`` admits the caller as the page's form post does
  (recording activity, so it needs a full-scope session), binds the operator's
  change document to the page's own forms, and prints the page's review with
  ``preview.token``, the same signed binding the page embeds. It changes
  nothing.
- ``schedule confirm`` takes that token (or one the page signed: tokens are
  bound to the Administrator, not the channel) and records the configuration
  request exactly as the page's **Confirm** does, rechecking authority, the
  applied configuration, the campaign's work and expiry. In the request's own
  durable transaction it records one ``admin_cmd_schedule_confirm`` event
  whose subject is the automation session. A repeated confirm returns the
  original request and records nothing new.

The change document (default, pending Administrator confirmation) is JSON::

    {"window": {"start_date": "...", "end_date": "...", "timezone": "...",
                "overlap_confirmed": true},
     "schedules": [{"id": "<saved>", "time": "10:00:00"},
                   {"id": "<saved>", "delete": true},
                   {"kind": "reminder", "date": "...", "time": "...",
                    "template_version": "<uuid>"}]}

A saved schedule named by ``id`` keeps every field the document leaves out;
``delete`` removes it; an entry without ``id`` adds a schedule. Saved
schedules the document does not name stay as they are: as on the page,
omission never removes one. ``window`` (any of its fields) changes the
campaign's dates only while they may still change; for a live campaign
(scheduled or open in Production, #912) only ``end_date`` may change, and
confirming it binds the exceptional end-date intent as the page does.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError
from django.http import QueryDict

from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request

# The page's limits: a signed preview of at most 256,000 characters, and a
# formset of at most 101 rows.
TOKEN_LIMIT = 256_000
CHANGES_LIMIT = 256_000
ROW_LIMIT = 101
WINDOW_FIELDS = ("timezone", "start_date", "end_date", "overlap_confirmed")
ROW_FIELDS = ("kind", "date", "time", "weekday", "template_version")
# What each field of a change document may hold, as JSON.
FIELD_TYPES = {
    "timezone": (str,),
    "start_date": (str,),
    "end_date": (str,),
    "overlap_confirmed": (bool,),
    "kind": (str,),
    "date": (str, type(None)),
    "time": (str,),
    "weekday": (int, type(None)),
    "template_version": (str,),
}


class InvalidChange(ValueError):
    """The change is not valid; nothing changed (``invalid``, exit 1).

    ``fields`` lists each problem as ``{"field", "code", "message"}``:
    ``code`` is the closed ``web.contracts.ErrorCode`` value (``required``
    or ``invalid``); the field is
    ``window.<name>``, ``schedules.<id>.<name>`` for a saved schedule,
    ``schedules.new<n>.<name>`` for the n-th added one (from 0), or
    ``window``/``schedules`` for a problem between fields; the message is the
    page's own.
    """

    def __init__(self, fields):
        """Keep the field problems the error document lists."""
        super().__init__("The change is not valid.")
        self.fields = fields


def _typed(name, value):
    """Whether ``value`` has a type the field ``name`` accepts (never a bool int)."""
    if isinstance(value, bool) and bool not in FIELD_TYPES[name]:
        return False
    return isinstance(value, FIELD_TYPES[name])


def parse_changes(text):
    """Parse and check the shape of a change document; values are the forms' job.

    Refuses (``ValueError``, ``invalid``) anything but an object with
    ``window`` and ``schedules``, unknown or mistyped members, a ``delete``
    without a saved ``id``, a repeated ``id``, a ``kind`` on a saved
    schedule (its mail type never changes), an empty new entry and more rows
    than the page takes. Returns ``{"window": dict, "schedules": list}``.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) - {"window", "schedules"}:
        raise ValueError("The change document has unknown members.")
    window = value.get("window", {})
    rows = value.get("schedules", [])
    if (
        not isinstance(window, dict)
        or set(window) - set(WINDOW_FIELDS)
        or not all(_typed(name, item) for name, item in window.items())
    ):
        raise ValueError("The change document's window is not valid.")
    if not isinstance(rows, list) or len(rows) > ROW_LIMIT:
        raise ValueError("The change document's schedules are not valid.")
    seen = set()
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) - {"id", "delete", *ROW_FIELDS}
            or not all(_typed(name, row[name]) for name in ROW_FIELDS if name in row)
        ):
            raise ValueError("A schedule in the change document is not valid.")
        if "id" in row:
            if not isinstance(row["id"], str) or row["id"] in seen:
                raise ValueError("A schedule is named twice or not by its id.")
            if "kind" in row:
                # A saved schedule's mail type never changes (as on the page).
                raise ValueError("A saved schedule's kind cannot change.")
            seen.add(row["id"])
        elif not row:
            raise ValueError("A new schedule needs its fields.")
        if "delete" in row and (
            row["delete"] is not True or "id" not in row or set(row) != {"id", "delete"}
        ):
            raise ValueError("Only a saved schedule can be deleted, by id alone.")
    return {"window": window, "schedules": rows}


def _text(value):
    """A value as the browser posts it: empty for None."""
    return "" if value is None else str(value)


def _window_initial(campaign):
    """The window's current values, as the page's form starts with them."""
    initial = {name: campaign[name] for name in ("timezone", "start_date", "end_date")}
    financial = campaign["financial"]
    if financial is not None:
        initial["overlap_confirmed"] = bool(financial["overlap_confirmed"])
    return initial


def form_data(changes, previous, campaign, *, editable, base_digest, end_only=False):
    """The page's posted form for this change document.

    ``previous`` is the campaign's saved schedule records and ``campaign``
    its applied values. Saved schedules are posted in the page's order
    (``schedule_order``), each with its own id, then the added ones, so the
    page's forms read exactly what a browser would have posted. A window
    change while the dates may not change is refused as the page refuses one
    (``StaleRecordError``); values equal to the current ones are no change.
    ``end_only`` (a live campaign, #912) admits a change to the end date
    alone.
    Returns ``(data, identifiers)``, the latter naming each form row for
    field errors.
    """
    from .accounts.schedule_forms import schedule_order
    from .storage import StaleRecordError

    rows = sorted(previous, key=schedule_order)
    saved = {row["id"] for row in rows}
    named = {entry["id"]: entry for entry in changes["schedules"] if "id" in entry}
    if set(named) - saved:
        raise InvalidChange(
            [
                {
                    "field": f"schedules.{identifier}",
                    "code": "invalid",
                    "message": "No saved schedule of this campaign has this id.",
                }
                for identifier in sorted(set(named) - saved)
            ]
        )
    added = [entry for entry in changes["schedules"] if "id" not in entry]
    data = QueryDict(mutable=True)
    data["action"] = "preview"
    data["base_digest"] = base_digest
    data["schedules-TOTAL_FORMS"] = str(len(rows) + len(added))
    data["schedules-INITIAL_FORMS"] = str(len(rows))
    identifiers = []
    for index, row in enumerate(rows):
        entry = named.get(row["id"], {})
        values = row["values"] | {
            name: entry[name] for name in ROW_FIELDS if name in entry
        }
        data[f"schedules-{index}-id"] = row["id"]
        for name in ROW_FIELDS:
            data[f"schedules-{index}-{name}"] = _text(values.get(name))
        if entry.get("delete"):
            data[f"schedules-{index}-DELETE"] = "on"
        identifiers.append(row["id"])
    for offset, entry in enumerate(added):
        index = len(rows) + offset
        for name in ROW_FIELDS:
            data[f"schedules-{index}-{name}"] = _text(entry.get(name))
        identifiers.append(f"new{offset}")
    initial = _window_initial(campaign)
    window = initial | changes["window"]
    if not editable:
        changed = {name for name in window if window[name] != initial.get(name)}
        # A live campaign's end date alone may still change (#912).
        if changed - ({"end_date"} if end_only else set()):
            raise StaleRecordError("Campaign dates are structurally locked.")
        if end_only:
            # The page posts its one open window field, changed or not.
            data["window-end_date"] = window["end_date"]
        return data, identifiers
    if set(window) - set(initial):
        raise InvalidChange(
            [
                {
                    "field": "window.overlap_confirmed",
                    "code": "invalid",
                    "message": "This campaign has no financial period.",
                }
            ]
        )
    for name in ("timezone", "start_date", "end_date"):
        data[f"window-{name}"] = window[name]
    if window.get("overlap_confirmed"):
        data["window-overlap_confirmed"] = "on"
    return data, identifiers


def _problem(field, item):
    """One field problem: the closed error code and the page's message.

    Django's ``required`` is ``ErrorCode.REQUIRED``; every other form
    error is ``ErrorCode.INVALID``.
    """
    from .web.contracts import ErrorCode

    code = ErrorCode.REQUIRED if item["code"] == "required" else ErrorCode.INVALID
    return {"field": field, "code": code.value, "message": str(item["message"])}


def field_errors(window, schedules, identifiers):
    """Every problem the page's forms found, with the page's messages."""
    problems = []

    def add(prefix, errors):
        """One entry per message; ``__all__`` is the form as a whole."""
        for name, messages in errors.get_json_data().items():
            field = prefix if name == "__all__" else f"{prefix}.{name}"
            problems.extend(_problem(field, item) for item in messages)

    add("window", window.errors)
    for index, form in enumerate(schedules.forms):
        add(f"schedules.{identifiers[index]}", form.errors)
    problems.extend(
        _problem("schedules", item)
        for item in schedules.non_form_errors().get_json_data()
    )
    return problems


@dataclass(frozen=True)
class SchedulePreview(ReadModel):
    """The page's review of a schedule change, and the token that confirms it.

    ``version`` is the applied configuration's digest the change was made
    against. ``window`` holds the dates and timezone ``before`` and
    ``after`` and the names of the campaign values that ``changed``. Each
    change names its schedule ``id``, its ``operation`` and ``kind``, its
    civil values and first resolved send times ``before`` and ``after``
    (None when added or removed), and its ``impact``: the page's counts of
    work already done, replaced, failed and blocking. ``blocking`` is their
    total of blocking work; while it is above zero, as on the page, there is
    no ``preview`` to confirm. Otherwise ``preview.token`` is the signed
    binding ``schedule confirm`` takes, valid for fifteen minutes.
    """

    campaign_id: UUID
    version: str
    window: dict
    changes: list
    blocking: int
    preview: dict | None


# The impact counts the page shows for each change.
IMPACT = ("delivered", "cancellable", "failed", "blocking", "occurrences", "outboxes")


def _side(value):
    """One side of a change: civil values and resolved send times, or None."""
    if value is None:
        return None
    return {
        "kind": value["kind"],
        "date": value["date"],
        "time": value["time"],
        "weekday": value["weekday"],
        "template_version": value["template_version"],
        "subject": value["subject"],
        "timezone": value["timezone"],
        "resolved": [
            {"key": slot["key"], "due_at": slot["due_at"]}
            for slot in value["resolved_slots"]
        ],
        "more": value["more_slots"],
    }


def _dates(values):
    """The campaign's dates and timezone from its values."""
    return {name: values[name] for name in ("start_date", "end_date", "timezone")}


def schedule_preview_model(context, version):
    """The command's projection of ``build_preview``'s review."""
    return SchedulePreview(
        campaign_id=context["campaign"].pk,
        version=version,
        window={
            "before": _dates(context["before_window"]),
            "after": _dates(context["after_window"]),
            "changed": sorted(context["window_changes"]),
        },
        changes=[
            {
                "id": change["id"],
                "operation": change["operation"],
                "kind": (change["after"] or change["before"])["kind"],
                "before": _side(change["before"]),
                "after": _side(change["after"]),
                "impact": {name: change["impact"].get(name, 0) for name in IMPACT},
            }
            for change in context["changes"]
        ],
        blocking=context["blocking"],
        preview=None if context["preview"] is None else {"token": context["preview"]},
    )


def _target(service, campaign_id):
    """The campaign a command names, or the current one (``not_available`` if none).

    Either way the configuration must be editable now: before setup, or
    during a restore under review, ``editable_configuration`` refuses with a
    ``ConfigError``, which the callers' ``_held`` reports as ``unavailable``.
    """
    from .accounts.admin_editing import editable_configuration

    current = editable_configuration(service).current_campaign_id
    target = campaign_id if campaign_id is not None else current
    if target is None:
        raise NotAvailable("There is no current campaign.")
    return target


def _admit_change(caller, service):
    """The page's admission of a change, recording activity as its post does.

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's signed-out refusal.
    """
    from .accounts.admin_editing import principal
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, service)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _ended_or_raise(caller, service, actor, error):
    """Re-raise a refusal, as exit 5 when the session ended meanwhile."""
    from .accounts.policy import Capability

    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    raise error


def preview_schedule(caller, service, campaign_id, *, expected_version, changes):
    """``schedule preview``: the Dates and mail schedules page's review of a change.

    Admits as the page's form post does (``principal``, recording activity),
    then, in the page's work transaction, finds the campaign as the page does
    (an unknown campaign is ``not_available``; one not current, or held by
    background work, ``stale_version``), binds the change document to the
    page's forms and builds the page's review. A base other than
    ``expected_version`` is ``stale_version``. Rechecks access afterwards.
    Changes nothing.
    """
    from .accounts.content_views import _records
    from .accounts.schedule_changes import build_preview, preview_salt
    from .accounts.schedule_forms import Schedules, ScheduleWindow, schedule_action
    from .accounts.schedule_reads import (
        campaign_schedules,
        live_end_at,
        schedule_state,
    )
    from .campaigns.work_locks import work_transaction

    actor = _admit_change(caller, service)
    parsed = parse_changes(changes)

    def step():
        """Build the review in one work transaction, as the page's post does."""
        with work_transaction():
            target = _target(service, campaign_id)
            try:
                state, campaign, editable = schedule_state(service, target)
            except KeyError:
                raise
            except LookupError:
                raise NotAvailable("No such campaign.") from None
            previous = campaign.active_configuration.values
            saved = campaign_schedules(state[0], target)
            # A live campaign's end date alone may still change (#912).
            live_at = None if editable else live_end_at(state, campaign)
            data, identifiers = form_data(
                parsed,
                saved,
                previous,
                editable=editable,
                base_digest=expected_version,
                end_only=live_at is not None,
            )
            window = ScheduleWindow(
                data,
                prefix="window",
                previous=previous,
                editable=editable,
                live_at=live_at,
            )
            schedule_action(data, window_fields=window.open_fields)
            schedules = Schedules(
                data,
                prefix="schedules",
                templates=_records(state[0], target),
                campaign_id=target,
                campaign=previous,
                previous=saved,
            )
            context = build_preview(
                service,
                actor,
                state,
                campaign,
                window,
                schedules,
                base_digest=expected_version,
                salt=preview_salt(target),
            )
            if context is None:
                raise InvalidChange(field_errors(window, schedules, identifiers))
            return schedule_preview_model(context, expected_version)

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    from .accounts.policy import Capability

    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


@dataclass(frozen=True)
class ScheduleConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def confirm_schedule(caller, service, campaign_id, *, token, context):
    """``schedule confirm``: the page's **Confirm** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token
    (expired or out of date is ``stale_version``, altered or for another
    campaign ``invalid``, another Administrator's ``denied``) and records the
    request, with ``admin_cmd_schedule_confirm`` in its own durable
    transaction. The configuration checks before intake run under ``_held``,
    so a restore under review or an activating change is ``unavailable``.

    ``context["committed"]`` is set once the request may have committed, so
    a later error is ``outcome_unknown`` (exit 6). A database error is
    treated so only once the request row was written (``attach`` ran): it
    may then have struck the commit. Before that, the transaction rolled
    back and nothing changed (exit 3). ``context["request_id"]`` is the
    request's id, fixed before intake (``policy_operation_id``), so the
    exit-6 document names it for ``config request show``; repeating with the
    same token, within its fifteen minutes, is also safe.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.schedule_changes import confirm_scope, preview_salt
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    try:
        target = _held(lambda: _target(service, campaign_id))
    except NotAvailable as error:
        _ended_or_raise(caller, service, actor, error)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=preview_salt(target))["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_SCHEDULE_CONFIRM,
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
        # Altered, or signed for another campaign (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ScheduleConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )


# The longest cancellation reason: the abort journal's column.
REASON_LIMIT = 1024
# ``config request cancel``'s refusal of a change still validating whose
# candidate was never prepared: no new machinery rescues it (#944).
UNPREPARED = {
    "field": "request",
    "code": "invalid",
    "message": (
        "This change's new settings were never prepared, so there is nothing "
        "to cancel yet. The configuration installer's log says what stops it; "
        "once that is fixed, the installer applies the change or refuses it."
    ),
}


@dataclass(frozen=True)
class ConfigCancel(ReadModel):
    """A live end-date change's cancellation and the request's status then.

    ``cancelled`` is false when this same cancellation was already recorded
    (a repeat), which changes nothing. The request is still unfinished until
    the configuration installer restores the previous settings and records
    it as failed; ``config request show`` follows it.
    """

    cancelled: bool
    request: dict


def cancel_end_change(caller, service, request_id, *, reason, context):
    """``config request cancel``: abort an unapplied live end-date change (#944).

    The operator's path out of a stuck change, which the data specification
    requires to go through the abort journal: in one durable transaction, as
    the installer's own lock order has it (the configuration lock, the
    runtime row, then the request), the current Administrator records the
    immutable ``CampaignConfigurationAbort`` with ``reason`` and the
    command's ``admin_cmd_config_request_cancel`` event. The journal's own
    trigger refuses a change that applied, has not started (``staged``) or
    whose base is no longer applied (``stale_version``). So does this
    command, more plainly, for a change already journaled with another
    reason (``stale_version``) and for one still ``validating`` whose
    candidate was never prepared (``invalid``, with ``UNPREPARED``'s
    message): that one has written nothing to undo, and only fixing what
    stops the installer moves it. The configuration
    installer then restores the previous YAML and records the request as
    failed, before any other request; nothing else is undone.

    A change that has not started is the change's own Administrator's to
    cancel, as Change status allows; another's is ``stale_version``, since
    the installer refuses or applies it within seconds. Only a request with
    a bound live end-date change is found here; any other is
    ``not_available``.
    """
    from django.db import IntegrityError, connection, transaction

    from .accounts.configuration_models import AppliedConfigurationVersion
    from .accounts.configuration_requests import _checkpoint, _status
    from .accounts.request_models import ConfigurationChangeRequest
    from .accounts.runtime_models import SystemConfiguration
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .campaigns.configuration_intents import journal_abort
    from .campaigns.live_end_date import admit_end_edit, end_intent
    from .campaigns.models import Campaign, CampaignConfigurationAbort
    from .observability import _guard_refusal, current_correlation
    from .storage import StaleRecordError

    reason = (reason or "").strip()
    if not reason or len(reason) > REASON_LIMIT:
        raise ValueError(f"Give a reason of at most {REASON_LIMIT} characters.")
    actor = _admit_change(caller, service)
    context["request_id"] = str(request_id)
    correlation = current_correlation()

    def cancel():
        """Journal the abort, or cancel an unstarted change; return whether new."""
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s, %s)", [736220, 1])
        runtime = SystemConfiguration.objects.select_for_update().get()
        intent = end_intent(request_id)
        request = (
            ConfigurationChangeRequest.objects.select_for_update(of=("self",))
            .filter(pk=request_id, authority="admin")
            .first()
        )
        if intent is None or request is None:
            raise NotAvailable("No live end-date change has this id.")
        status = _status(request)
        abort = CampaignConfigurationAbort.objects.filter(intent=intent).first()
        if abort is not None and abort.reason == reason:
            return False
        if status.state == "staged" and request.actor_id == actor.identity:
            _checkpoint(
                request,
                sequence=status.sequence + 1,
                state="cancelled",
                actor_id=actor.identity,
                correlation_id=correlation,
            )
            return True
        if status.state in {"staged", "applied", "failed", "cancelled"}:
            raise StaleRecordError("This change can no longer be cancelled here.")
        if abort is not None:
            # Journaled already (by another cancellation or the installer's
            # own refusal) with another reason, and not yet restored: the
            # journal is immutable, so say so rather than let journal_abort's
            # invariant surface as an internal error.
            raise StaleRecordError(
                "This change is already being cancelled; config request show "
                "follows it."
            )
        if not AppliedConfigurationVersion.objects.filter(
            pk=request.candidate_version_id,
            digest=request.candidate_digest,
            predecessor_id=request.base_id,
        ).exists():
            # Stuck validating before its candidate was prepared: the journal
            # (and its restore) needs a prepared candidate, and nothing was
            # written yet that needs undoing. Refused as ``invalid`` because
            # only that code carries a message, and this one says what to do.
            raise InvalidChange([UNPREPARED])
        campaign = Campaign.objects.select_for_update().get(pk=intent.campaign_id)
        admit_end_edit("abort_configuration", campaign, runtime, intent)
        journal_abort(
            intent, reason=reason, actor_id=actor.identity, correlation_id=correlation
        )
        return True

    try:
        with transaction.atomic(durable=True):
            created = cancel()
            if created:
                record_action(
                    Action.ADMIN_CMD_CONFIG_REQUEST_CANCEL,
                    actor_kind=ActorKind.PORTAL_USER,
                    actor_id=actor.identity,
                    subject_id=caller.automation_session_id,
                    context={"outcome": Outcome.SUCCEEDED},
                )
            status = _status(ConfigurationChangeRequest.objects.get(pk=request_id))
    except IntegrityError as error:
        if not _guard_refusal(error):
            raise
        # The journal's trigger: applied, or its base no longer applied.
        _ended_or_raise(
            caller,
            service,
            actor,
            StaleRecordError("This change can no longer be cancelled."),
        )
    except (NotAvailable, StaleRecordError, PermissionError, InvalidChange) as error:
        _ended_or_raise(caller, service, actor, error)
    context["committed"] = True
    return ConfigCancel(cancelled=created, request=config_request(status).to_document())
