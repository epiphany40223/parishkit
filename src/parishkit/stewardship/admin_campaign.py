"""Campaign settings commands (ADM-11 PR 10): show, preview and confirm.

Campaign settings (``admin:campaign_settings``, #1119) is one editor and one
Review for every case: a Testing-mode draft's settings, a live campaign's
end date and Ministry leader roles, and its Ministries. The commands go
through the page's own request-free pieces, so the page and the command
line cannot drift: its pinned state (``campaign_views._state``), the
current-campaign guards (``_target``), what can change now (``_openness``),
its form (``_settings_form``, ``CampaignSettingsForm``), its Review
(``review_settings``) and its Apply routing (``confirm_route``).

- ``campaign show`` admits passively with the page's ``CONFIGURE``
  capability (Administrators only; a read-only session reads), reads in
  one read transaction, rechecks the session and records nothing, as the
  page's GET records nothing. It prints the current campaign's settings in
  the change document's shape and which of them can change now.
- ``campaign preview`` admits as the page's Review post does (recording
  activity, so it needs a full-scope session), binds the change document to
  the page's form in the page's work transaction and runs the page's Review,
  printing ``preview.token``, the same signed binding the page's Apply
  posts. It changes nothing and records no event, as the page's Review
  records none.
- ``campaign confirm`` takes that token (or one the page signed) and
  records the configuration request exactly as the page's Apply does,
  through ``admin_editing.confirm_intent`` with the salt, scope and
  companion ``confirm_route`` picks: an end-date change records
  ``campaign_end_date_requested`` and a Ministries change
  ``campaign_ministries_requested``, as on the page. In the request's own
  durable transaction it also records one ``admin_cmd_campaign_confirm``
  event. A repeat returns the original request and records nothing new.

None is fresh-gated or prompts, as the page asks for no recent sign-in and
its Apply is the reviewed token. Only the current campaign is edited, as
the page's address names no other; creating or copying a campaign stays
refused (#145).

The change document (default, pending Administrator confirmation) has the
shape of ``campaign show``'s ``settings`` member, keyed by the page's form
field names::

    {"campaign": {"name": "Stewardship 2027", "ministry_leader_roles": ["Chair"]}}

Any non-empty subset of the fields; a field the document leaves out keeps
its saved value. Booleans are JSON booleans, the Ministry, fund and leader
role lists JSON arrays, and the rest strings (``year_label`` may be null).
"""

import json
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from django.db import DatabaseError

from .admin_changes import InvalidChange, _admit_change, _ended_or_raise, _problem
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request
from .admin_settings import _read

CHANGES_LIMIT = 256_000
# Fields that may be null in a change document: only the optional label.
NULLABLE = frozenset({"year_label"})


@dataclass(frozen=True)
class CampaignShow(ReadModel):
    """The current campaign's settings, as Campaign settings draws them.

    ``version`` is what ``campaign preview --expected-version`` takes.
    ``editable`` says the whole Testing-mode draft can change; ``open``
    lists the fields that can change now (a live campaign's end date,
    leader roles and Ministries, while no background work holds them), in
    the form's order. ``settings`` is each form field's saved value, in the
    change document's shape.
    """

    version: str
    campaign_id: UUID
    state: str
    editable: bool
    open: list
    settings: dict


@dataclass(frozen=True)
class CampaignPreview(ReadModel):
    """The page's review of a Campaign settings change, and its token.

    ``kind`` is which review it is: ``settings`` (a draft's settings or a
    live campaign's leader roles), ``end_date`` (a live campaign's end
    date, with any leader-role change) or ``ministries`` (a live
    campaign's Ministries). Each change names its stored ``field`` and its
    ``before`` and ``after`` values. ``replanned`` lists the ids of the
    schedules an end-date change re-plans; ``ministries`` is a Ministries
    review's ``added`` and ``removed`` (each with its counts) and
    ``open_forms``, else None. ``preview.token`` is the signed binding
    ``campaign confirm`` takes, valid for fifteen minutes.
    """

    version: str
    campaign_id: UUID
    kind: str
    changes: list
    replanned: list
    ministries: dict | None
    notes: list
    preview: dict


@dataclass(frozen=True)
class CampaignConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _fields():
    """The page's form fields, in its order, with their kinds.

    Returns ``(names, booleans, multiples)``: every field but the hidden
    version, the check boxes, and the lists (``campaign_views``'
    ``MULTIPLE_FIELDS``).
    """
    from django import forms

    from .accounts.campaign_forms import CampaignSettingsForm
    from .accounts.campaign_views import MULTIPLE_FIELDS

    fields = {
        name: field
        for name, field in CampaignSettingsForm.base_fields.items()
        if name != "base_digest"
    }
    booleans = {
        name for name, field in fields.items() if isinstance(field, forms.BooleanField)
    }
    return list(fields), booleans, MULTIPLE_FIELDS


def parse_settings(text):
    """Parse a change document's shape; the values are the page form's job.

    Refuses (``ValueError``, ``invalid``) anything but an object whose only
    member is a non-empty ``campaign`` object. A member that is not a form
    field, or whose JSON type the field cannot take, is ``InvalidChange`` at
    ``campaign.<name>``.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) != {"campaign"}:
        raise ValueError("The change document must have only a campaign object.")
    fields = value["campaign"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("The change document's campaign is not valid.")
    names, booleans, multiples = _fields()
    problems = []
    for name, item in fields.items():
        if name not in names:
            message = "This is not a Campaign settings field."
        elif name in booleans:
            message = None if isinstance(item, bool) else "Give true or false."
        elif name in multiples:
            good = isinstance(item, list) and all(
                isinstance(entry, (str, int)) and not isinstance(entry, bool)
                for entry in item
            )
            message = None if good else "Give a list."
        elif item is None:
            message = None if name in NULLABLE else "Give a value."
        else:
            message = None if isinstance(item, str) else "Give a string."
        if message:
            problems.append(
                {"field": f"campaign.{name}", "code": "invalid", "message": message}
            )
    if problems:
        raise InvalidChange(problems)
    return fields


def _plain(value):
    """A form initial value as JSON: dates as ISO strings, the rest as is."""
    if isinstance(value, date):
        return value.isoformat()
    return list(value) if isinstance(value, (list, tuple)) else value


def saved_settings(form):
    """Each form field's saved value, in the change document's shape.

    The form's own initial values, which the page's editor shows: check
    boxes as booleans, lists as lists (empty when unset), the rest as
    strings or None.
    """
    names, booleans, multiples = _fields()
    settings = {}
    for name in names:
        value = _plain(form.get_initial_for_field(form.fields[name], name))
        if name in booleans:
            value = bool(value)
        elif name in multiples:
            value = list(value or [])
        settings[name] = value
    return settings


def form_data(form, open_fields, fields, expected_version):
    """The page's Review post: the open fields as saved, then the document.

    The page posts only its open fields (a disabled field is not sent), so
    a locked field is posted only when the document names it, and the
    page's locked-field check then refuses it if it changes. A null is the
    blank field; lists are posted as strings, as the page's choices are.
    """
    _names, booleans, multiples = _fields()
    saved = saved_settings(form)
    data = {name: saved[name] for name in saved if name in open_fields}
    data.update(fields)
    post = {"base_digest": expected_version}
    for name, value in data.items():
        if name in multiples:
            post[name] = [str(item) for item in value]
        elif name in booleans:
            # The page sends a ticked box only.
            if value:
                post[name] = "on"
        else:
            post[name] = "" if value is None else value
    return post


def _comparable(value):
    """A setting's value as compared for a change: lists as sorted strings.

    The page's form stores its Ministry and fund lists sorted and its
    leader roles in canonical order, so an order alone is no change; a
    blank label is the null one.
    """
    if isinstance(value, list):
        return sorted(str(item) for item in value)
    return "" if value is None else value


def locked_changes(saved, open_fields, fields):
    """The document's fields that are locked now and differ from their saved value.

    The page cannot post such a change: a locked field is disabled, and a
    locked check box cannot be unticked. Bound to the form, an unticked box
    would not be posted at all and the change would be lost without a word,
    so the command refuses it first, with the page's lock refusal.
    """
    return [
        name
        for name, value in fields.items()
        if name not in open_fields and _comparable(value) != _comparable(saved[name])
    ]


def field_errors(form):
    """Every problem the page's form found, with the page's messages.

    The field is ``campaign.<name>``, or ``campaign`` for the form as a
    whole.
    """
    problems = []
    for name, messages in form.errors.get_json_data().items():
        field = "campaign" if name == "__all__" else f"campaign.{name}"
        problems.extend(_problem(field, item) for item in messages)
    return problems


def _refusal(error):
    """A page refusal (``UserFacingError``) as an ``invalid`` change problem."""
    refusal = error.refusal
    text = " ".join(str(item) for item in (refusal.message, refusal.fix) if item)
    return InvalidChange([{"field": "campaign", "code": "invalid", "message": text}])


def _editor(service):
    """The page's state, current campaign and settings form, as its view builds them.

    Returns ``(state, campaign, editable, opened, reason, form)``, where
    ``form(data=None)`` builds the page's form from that same state, bound
    to ``data`` as the page's Review post binds it. With no current
    campaign, ``not_available``.
    """
    from .accounts.campaign_views import (
        _lock_reason,
        _openness,
        _settings_form,
        _state,
        _target,
    )

    state = _state(service)
    configuration, campaigns, source, held = state[:4]
    if configuration.current_campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    try:
        campaign, editable = _target(
            configuration, campaigns, held, configuration.current_campaign_id
        )
    except LookupError:
        raise NotAvailable("There is no current campaign.") from None
    opened = _openness(state, campaign, editable)
    open_fields, live_at, reasons = opened
    reason = _lock_reason(campaign, held)

    def form(data=None):
        """The page's settings form, locked as the page locks it."""
        return _settings_form(
            configuration,
            campaign,
            data,
            source=source,
            open_fields=open_fields,
            reason=reason,
            reasons=reasons,
            live_end=live_at is not None,
        )

    return state, campaign, editable, opened, reason, form


def read_campaign(caller, service):
    """``campaign show``: the current campaign's settings and what is open."""

    def read():
        """The page's editor as it opens, as values."""
        state, campaign, editable, opened, _reason, form = _editor(service)
        names, _booleans, _multiples = _fields()
        return CampaignShow(
            version=state[0].active_configuration.digest,
            campaign_id=campaign.pk,
            state=campaign.state,
            editable=editable,
            open=[name for name in names if name in opened[0]],
            settings=saved_settings(form()),
        )

    return _read(caller, service, read)


def preview_campaign(caller, service, *, expected_version, changes):
    """``campaign preview``: Campaign settings' review of a change.

    Admits as the page's Review post does, then, in the page's work
    transaction, binds the document to the page's form (only the open
    fields, as the page posts them) and runs the page's ``review_settings``.
    A document field locked now that differs from its saved value is
    refused first with the page's lock refusal (``locked_changes``,
    ``stale_version``), as is a base other than ``expected_version``; the
    form's field errors, the page's refusals ("No settings have changed.",
    Ministries with other changes, mail being sent now) and a candidate the
    configuration refuses are ``invalid``. A draft's dates-only change
    while it has mail schedules is ``invalid`` naming ``schedule
    preview``, where the page sends it. Rechecks access afterwards.
    Changes nothing.
    """
    from parishkit.config import ConfigError

    from .accounts.campaign_views import locked_refusal, review_settings
    from .accounts.policy import Capability
    from .campaigns.work_locks import work_transaction
    from .web.refusals import UserFacingError

    actor = _admit_change(caller, service)
    fields = parse_settings(changes)

    def step():
        """Bind, review and sign in one work transaction, as the page's post does."""
        with work_transaction():
            state, campaign, editable, opened, reason, build = _editor(service)
            saved = build()
            locked = locked_changes(saved_settings(saved), opened[0], fields)
            if locked:
                # The page's own refusal for a setting locked now.
                raise locked_refusal(locked[0], reason, opened[2])
            form = build(form_data(saved, opened[0], fields, expected_version))
            try:
                review = review_settings(
                    service, actor, state, campaign, editable, opened, form, reason
                )
            except UserFacingError as error:
                raise _refusal(error) from None
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the
                # page's review would.
                raise ValueError("The configuration refuses this change.") from None
            if review is None:
                raise InvalidChange(field_errors(form))
            if "reconcile" in review:
                raise InvalidChange(
                    [
                        {
                            "field": "campaign",
                            "code": "invalid",
                            "message": (
                                "This campaign has mail schedules: change its "
                                "dates with schedule preview, which reconciles "
                                "every mailing with the new dates."
                            ),
                        }
                    ]
                )
        return CampaignPreview(
            version=expected_version,
            campaign_id=campaign.pk,
            kind=review["kind"],
            changes=[
                {"field": name, "before": before, "after": after}
                for name, (before, after) in review["values"].items()
            ],
            replanned=[str(item) for item in review.get("replanned", [])],
            ministries=_ministries(review.get("ministries")),
            notes=[str(note) for note in review["notes"]],
            preview={"token": review["preview"]},
        )

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def _ministries(review):
    """A Ministries review as plain values: names as strings, counts as numbers."""
    if review is None:
        return None
    return {
        "added": [row | {"name": str(row["name"])} for row in review["added"]],
        "removed": [row | {"name": str(row["name"])} for row in review["removed"]],
        "open_forms": review["open_forms"],
    }


def confirm_campaign(caller, service, *, token, context):
    """``campaign confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the salt, scope and companion the page's ``confirm_route`` picks for
    the current campaign (expired or out of date is ``stale_version``;
    altered or another page's ``invalid``; another Administrator's
    ``denied``) and records the request, the page's companion event and
    ``admin_cmd_campaign_confirm`` in its own durable transaction. The
    configuration must be editable first (``_held``), so a restore under
    review or an activating change is ``unavailable``.
    ``context["committed"]`` and ``context["request_id"]`` follow ``parish
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.campaign_views import confirm_route
    from .accounts.configuration_requests import policy_operation_id
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    configuration = _held(lambda: editable_configuration(service))
    if configuration.current_campaign_id is None:
        # An ended session is exit 5, not this refusal.
        _ended_or_raise(
            caller, service, actor, NotAvailable("There is no current campaign.")
        )
    route = confirm_route(token, configuration.current_campaign_id)
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=route["salt"])["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the page's companion, then the command's event."""
        if route["attach"] is not None:
            route["attach"](request, extra)
        record_action(
            Action.ADMIN_CMD_CAMPAIGN_CONFIRM,
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
            salt=route["salt"],
            link=None,
            current_scope=route["current_scope"],
            attach=attach,
        )
    except signing.BadSignature:
        # Altered, or signed by another page (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return CampaignConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
