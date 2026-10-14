"""Integration settings change commands (ADM-11 PR 10).

``integration preview`` and ``integration confirm`` are an integration
settings page's non-secret **Save** (its review, then **Apply**) and its
**Remove**, through the page's own request-free pieces in
``accounts.integration_views`` (``review_settings``, ``review_removal``, the
page's form and salt) and ``admin_editing.confirm_intent``, so the page and
the command line cannot drift:

- ``integration preview TARGET`` admits as the page's post does (recording
  activity, so it needs a full-scope session), stands in for the page's
  recent sign-in with the live session (``require_fresh``, #547; a preview
  records no notice), binds the change document to the page's own
  ``IntegrationForm`` in one read snapshot and prints the page's review
  with ``preview.token``, the same signed binding the page embeds. With
  ``--remove`` it is the page's Remove review instead. It changes nothing
  and records no event, as the page's review records none.
- ``integration confirm TARGET`` takes that token (or one the page signed:
  tokens are bound to the Administrator, not the channel) and records the
  configuration request exactly as the page's **Apply** does, with the
  page's fresh gate under the work lock (which records the
  ``automation_fresh_gate`` event and ``fresh_gated`` notice). In the
  request's own durable transaction it records one
  ``admin_cmd_integration_confirm`` event whose subject is the automation
  session; a Slack change or removal also leaves a ``policy_change``
  notice and observes ``automation_policy_change`` (admin-automation
  specification, "Notifications"). A repeated confirm returns the original
  request and records nothing new.

The change document (default, pending Administrator confirmation) has the
shape of ``integration show``'s ``settings`` member::

    {"settings": {"sender": "office@example.org", "sender_name": null}}

Any non-empty subset of the page's fields for the target; a field the
document leaves out keeps its applied value. Only ``sender_name`` may be
null or empty (the page then omits it and mail uses the parish name).
ParishSoft's refresh schedule is not part of it (#632 adds
``--refresh-schedule``): an unchanged schedule keeps its stored keys
exactly, as the page's save does. Keys are never accepted here; Slack,
which its first key sets up, cannot be set up by settings alone.
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import InvalidChange, _admit_change, _ended_or_raise, _problem
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request

CHANGES_LIMIT = 64_000
# The settings that may be null (or empty): the optional From name.
NULLABLE = frozenset({"sender_name"})


def setting_names(target):
    """The page's settings fields for ``target``, in the page's order."""
    from .accounts.integration_forms import IntegrationForm

    return [name for name in IntegrationForm(target).fields if name != "base_digest"]


def parse_settings(text, target):
    """Parse a change document's shape; the values are the page form's job.

    Refuses (``ValueError``, ``invalid``) anything but an object whose only
    member is a non-empty ``settings`` object of strings, integers or nulls.
    A member that is not one of the target's settings, or a null other than
    ``sender_name``, is ``InvalidChange`` at ``settings.<name>``.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) != {"settings"}:
        raise ValueError("The change document must have only a settings object.")
    fields = value["settings"]
    if not isinstance(fields, dict) or not fields:
        raise ValueError("The change document's settings are not valid.")
    names = setting_names(target)
    problems = [
        {
            "field": f"settings.{name}",
            "code": "invalid",
            "message": "This is not one of this integration's settings.",
        }
        for name in sorted(set(fields) - set(names))
    ]
    problems += [
        {
            "field": f"settings.{name}",
            "code": "required",
            "message": "Give a value; only sender_name may be null.",
        }
        for name in sorted(fields)
        if name in names and fields[name] is None and name not in NULLABLE
    ]
    if problems:
        raise InvalidChange(problems)
    if not all(
        item is None or (isinstance(item, (str, int)) and not isinstance(item, bool))
        for item in fields.values()
    ):
        raise ValueError("Each setting must be a string, a number or null.")
    return fields


def form_data(stored, names, values, expected_version):
    """The page's form post: the applied settings, then the document's values.

    ``names`` are the page's fields and ``values`` the document's. Omitted
    fields keep the applied value the page's editor shows, so the review
    reports only what the document changes; a null is the blank field. No
    refresh schedule rows are posted, so the page's schedule editor keeps
    the stored schedule exactly.
    """
    data = {name: str(stored.get(name, "")) for name in names}
    data.update(
        {name: "" if value is None else str(value) for name, value in values.items()}
    )
    data["base_digest"] = expected_version
    return data


def field_errors(form):
    """Every problem the page's form found, with the page's messages.

    The field is ``settings.<name>``, or ``settings`` for the form as a
    whole (no change).
    """
    problems = []
    for name, messages in form.errors.get_json_data().items():
        field = "settings" if name == "__all__" else f"settings.{name}"
        problems.extend(_problem(field, item) for item in messages)
    return problems


@dataclass(frozen=True)
class IntegrationPreview(ReadModel):
    """The page's review of an integration settings change, and its token.

    ``version`` is the applied configuration's digest the change was made
    against; ``removing`` whether this is the page's Remove. Each change
    names its ``field`` and the stored values ``before`` and ``after`` (None
    for an absent setting), in the page's order; a removal lists none.
    ``preview.token`` is the signed binding ``integration confirm`` takes,
    valid for fifteen minutes.
    """

    version: str
    target: str
    name: str
    removing: bool
    changes: list
    preview: dict


@dataclass(frozen=True)
class IntegrationConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _review(service, actor, configuration, target, fields, expected_version):
    """The page's Save review of ``fields``: ``(changes, token)``.

    As the page's ``_preview``: an off-site folder is refused in the local
    environment, a required integration with no record is the page's 404
    (``not_available``), and Slack not yet set up needs its key (the page's
    refusal at the key field). The page's field errors and "No settings
    have changed." are ``invalid`` at each field.
    """
    from .accounts.integration_forms import SETUP_KEY_FIELDS
    from .accounts.integration_views import (
        _form,
        _optional,
        _refuse_drive_in_local,
        review_settings,
    )

    if target == "backup":
        _refuse_drive_in_local()
    try:
        record = _optional(configuration, target)
    except LookupError:
        raise NotAvailable("That integration is not set up.") from None
    if record is None and target != "backup":
        raise InvalidChange(
            [
                {
                    "field": "key",
                    "code": "required",
                    "message": str(SETUP_KEY_FIELDS[target][2]),
                }
            ]
        )
    stored = {} if record is None else record["values"]["settings"]
    names = setting_names(target)
    form = _form(
        target, form_data(stored, names, fields, expected_version), stored=record
    )
    review = review_settings(service, actor, configuration, target, form)
    if review is None:
        raise InvalidChange(field_errors(form))
    before, after = review["before"], review["after"]
    changes = [
        {"field": name, "before": before.get(name), "after": after.get(name)}
        for name in names
        if before.get(name) != after.get(name)
    ]
    return changes, review["preview"]


def preview_integration(
    caller, service, target, *, expected_version, changes=None, remove=False
):
    """``integration preview``: an integration settings page's review.

    Admits as the page's post does, passes the page's fresh gate with the
    live full-scope session, then, in one read snapshot, checks the version
    (another one is ``stale_version``) and builds the page's own review of
    the change document, or of removing the integration (``remove``): one
    that cannot be removed or is not set up is ``not_available``, as the
    page's 404. A resulting document the configuration refuses is
    ``invalid``. Rechecks access afterwards. Changes nothing.
    """
    from django.db import transaction

    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import review_removal
    from .accounts.policy import Capability
    from .accounts.sessions import require_fresh
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit_change(caller, service)
    fields = None if remove else parse_settings(changes, target)

    def step():
        """Pass the fresh gate, then check and review in one snapshot."""
        # The page asks for a recent sign-in before its review (#547); the
        # session's FOR SHARE lock needs a writable transaction, so it is
        # checked just before the read-only snapshot. A preview has no
        # command type, so it records no notice.
        with transaction.atomic():
            require_fresh(caller)
        with read_transaction():
            configuration = editable_configuration(service)
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            try:
                if remove:
                    try:
                        token = review_removal(service, actor, configuration, target)
                    except LookupError:
                        raise NotAvailable(
                            "That integration cannot be removed."
                        ) from None
                    found = []
                else:
                    found, token = _review(
                        service, actor, configuration, target, fields, expected_version
                    )
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the
                # page's review would.
                raise ValueError("The configuration refuses this change.") from None
        return IntegrationPreview(
            version=expected_version,
            target=target,
            name=str(LABELS[target]),
            removing=remove,
            changes=found,
            preview={"token": token},
        )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def prompt_lines(target, token):
    """The confirmation prompt's summary, from the token's reviewed patch.

    Names the integration and what Apply does, never a setting's value. A
    token that is not this target's is ``invalid`` before the prompt (an
    expired one is checked properly by ``confirm_intent`` afterwards).
    """
    from django.core import signing

    from .accounts.integration_forms import LABELS
    from .accounts.integration_views import SALT

    try:
        [operation] = signing.loads(token, salt=SALT + target)["patch"]
        kind = operation["operation"]
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    label = LABELS[target]
    if kind == "remove":
        return [f"This removes {label}."]
    names = ", ".join(sorted(operation["values"].get("settings", {})))
    return [f"This applies the reviewed {label} settings ({names})."]


def confirm_integration(caller, service, target, *, token, context):
    """``integration confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for ``target`` (expired or out of date is
    ``stale_version``, altered or another target's ``invalid``, another
    Administrator's ``denied``), passes the page's fresh gate again under
    the work lock and records the request with
    ``admin_cmd_integration_confirm`` (and, for Slack, the policy notice)
    in its own durable transaction. The configuration must be editable
    first (``_held``), so a restore under review or an activating change is
    ``unavailable``. ``context["committed"]`` and ``context["request_id"]``
    follow ``parish confirm``'s rules, so a later error is
    ``outcome_unknown`` naming the request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.automation_sessions import notify, observe
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.integration_views import SALT
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    _held(lambda: editable_configuration(service))
    salt = SALT + target
    try:
        # The request's id is derived from the signed key, before intake;
        # confirm_intent verifies the token properly (expiry included).
        key = UUID(signing.loads(token, salt=salt)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []

    def attach(request, extra):
        """Record the command's event (and Slack's notice) with the request."""
        record_action(
            Action.ADMIN_CMD_INTEGRATION_CONFIRM,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        if target == "slack":
            # Slack notifications are notification settings: changing or
            # removing them tells every Administrator (admin-automation
            # specification, "Notifications").
            notify(
                "policy_change",
                caller.automation_session,
                command_type=caller.command_type,
                actor_id=actor.identity,
            )
            observe(IncidentKind.AUTOMATION_POLICY_CHANGE)
        created.append(request.pk)

    try:
        receipt = confirm_intent(
            caller,
            service,
            actor,
            token=token,
            salt=salt,
            link=None,
            current_scope=lambda service: (editable_configuration(service), None),
            attach=attach,
            # Checked again under the work lock, as the page's Apply (#547).
            fresh=True,
        )
    except signing.BadSignature:
        # Altered, or signed for another integration or page (an expired
        # one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return IntegrationConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
