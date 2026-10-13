"""Parish profile change commands of the Admin automation command line (ADM-11 PR 10).

``parish preview`` and ``parish confirm`` change the parish profile as the
Parish settings page does, through the same functions
(``parish_views.ParishForm`` and ``review_change``,
``admin_editing.confirm_intent``), as ``schedule preview`` and ``schedule
confirm`` change schedules:

- ``parish preview`` admits the caller as the page's Review post does
  (recording activity, so it needs a full-scope session), binds the change
  document to the page's own form, and prints the page's review with
  ``preview.token``, the same signed binding the page embeds. It changes
  nothing and records no event, as the page's Review records none.
- ``parish confirm`` takes that token (or one the page signed: tokens are
  bound to the Administrator, not the channel) and records the
  configuration request exactly as the page's **Apply** does. In the
  request's own durable transaction it records one
  ``admin_cmd_parish_confirm`` event whose subject is the automation
  session; the page records no other. A repeated confirm returns the
  original request and records nothing new.

The change document (default, pending Administrator confirmation) has the
shape of ``parish show``'s ``parish`` member::

    {"parish": {"phone": "(212) 555-1234", "online_giving_url": null}}

Any non-empty subset of the profile's fields; a field the document leaves
out keeps its applied value, and ``online_giving_url`` null (or "") removes
the giving link. No other field may be null, and ``date_format`` may not be
blank: the page's form would read a blank date format as the default style.
Values are typed as on the page: a telephone any common way, which is
stored as E.164.
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import InvalidChange, _admit_change, _ended_or_raise, _problem
from .admin_reads import ReadModel, _held, _recheck, config_request

TOKEN_LIMIT = 256_000
CHANGES_LIMIT = 256_000


def parse_profile(text):
    """Parse a change document's shape; the values are the page form's job.

    Refuses (``ValueError``, ``invalid``) anything but an object whose only
    member is a non-empty ``parish`` object of strings or nulls. A member
    that is not a profile field, a null other than ``online_giving_url``,
    or a blank ``date_format`` is ``InvalidChange`` at ``parish.<name>``.
    """
    from .accounts.parish_views import PROFILE_FIELDS

    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) != {"parish"}:
        raise ValueError("The change document must have only a parish object.")
    fields = value["parish"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("The change document's parish is not valid.")
    unknown = sorted(set(fields) - set(PROFILE_FIELDS))
    if unknown:
        raise InvalidChange(
            [
                {
                    "field": f"parish.{name}",
                    "code": "invalid",
                    "message": "This is not a parish profile field.",
                }
                for name in unknown
            ]
        )
    if not all(item is None or isinstance(item, str) for item in fields.values()):
        raise ValueError("Each parish value must be a string or null.")
    # Only the giving link may be removed. The page's form would read any
    # other null as a blank field, and a blank date format as the default
    # style (an older form post), silently resetting it; on the command line
    # a date format must be named.
    blank = sorted(
        name
        for name, item in fields.items()
        if (item is None and name != "online_giving_url")
        or (name == "date_format" and not item)
    )
    if blank:
        raise InvalidChange(
            [
                {
                    "field": f"parish.{name}",
                    "code": "required",
                    "message": "Give a value; only online_giving_url may be null.",
                }
                for name in blank
            ]
        )
    return fields


def form_data(configuration, fields, expected_version):
    """The page's form post: the applied profile, then the document's values.

    Omitted fields keep the applied value the page's editor would show
    (``_initial_form``'s), so the page's review reports only what the
    document changes. A null giving link is the blank field.
    """
    from .accounts.parish_views import DEFAULTS, PROFILE_FIELDS, _profile

    record = _profile(configuration)["values"]
    data = {name: record.get(name, DEFAULTS.get(name, "")) for name in PROFILE_FIELDS}
    data.update({name: value or "" for name, value in fields.items()})
    data["base_digest"] = expected_version
    return data


def field_errors(form):
    """Every problem the page's form found, with the page's messages.

    The field is ``parish.<name>``, or ``parish`` for the form as a whole
    (no change, or a document the configuration refuses).
    """
    problems = []
    for name, messages in form.errors.get_json_data().items():
        field = "parish" if name == "__all__" else f"parish.{name}"
        problems.extend(_problem(field, item) for item in messages)
    return problems


@dataclass(frozen=True)
class ParishPreview(ReadModel):
    """The page's review of a parish profile change, and its token.

    ``version`` is the applied configuration's digest the change was made
    against. Each change names its ``field`` and the stored values
    ``before`` and ``after`` (an E.164 telephone, a ``date_format`` key,
    None for no giving link), in the profile's order. ``notes`` are the
    page's review notes (the timezone note when the timezone changes).
    ``preview.token`` is the signed binding ``parish confirm`` takes, valid
    for fifteen minutes.
    """

    version: str
    changes: list
    notes: list
    preview: dict


@dataclass(frozen=True)
class ParishConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def preview_parish(caller, service, *, expected_version, changes):
    """``parish preview``: the Parish settings page's review of a change.

    Admits as the page's Review post does, then, in one read-only snapshot,
    binds the document to the page's form and builds the page's review
    (``review_change``). The page's field errors, no change, and a document
    the configuration refuses are ``invalid`` with each problem's field; a
    base other than ``expected_version`` is ``stale_version``. Rechecks
    access afterwards. Changes nothing.
    """
    from .accounts.admin_editing import editable_configuration
    from .accounts.parish_views import ParishForm, review_change
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction

    actor = _admit_change(caller, service)
    fields = parse_profile(changes)

    def step():
        """Validate, review and sign in one snapshot, as the page's Review does."""
        with read_transaction():
            configuration = editable_configuration(service)
            form = ParishForm(form_data(configuration, fields, expected_version))
            if not form.is_valid():
                raise InvalidChange(field_errors(form))
            review = review_change(service, actor, configuration, form)
            if review is None:
                raise InvalidChange(field_errors(form))
        return ParishPreview(
            version=expected_version,
            changes=[
                {"field": name, "before": before, "after": after}
                for name, (before, after) in review["values"].items()
            ],
            notes=[str(note) for note in review["notes"]],
            preview={"token": review["preview"]},
        )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm_parish(caller, service, *, token, context):
    """``parish confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token
    (expired or out of date is ``stale_version``, altered or another page's
    ``invalid``, another Administrator's ``denied``) and records the request
    with ``admin_cmd_parish_confirm`` in its own durable transaction. The
    configuration must be editable first (``_held``), so a restore under
    review or an activating change is ``unavailable``.
    ``context["committed"]`` and ``context["request_id"]`` follow ``schedule
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.parish_views import SALT, _scope
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    _held(lambda: editable_configuration(service))
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=SALT)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_PARISH_CONFIRM,
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
            salt=SALT,
            link=None,
            current_scope=_scope,
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
    return ParishConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
