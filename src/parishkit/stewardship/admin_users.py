"""User change commands of the Admin automation command line (#952 slice 5).

``users preview`` and ``users confirm`` change Admin portal users as the
Users page does, through the same functions (``accounts.user_changes`` and
``admin_editing.confirm_intent``), as ``schedule preview`` and ``schedule
confirm`` change schedules:

- ``users preview`` admits the caller as the page's Review does (recording
  activity, so it needs a full-scope session), plans the change document
  with the page's checks and prints each user's Name and role before and
  after, with ``preview.token``, the same signed binding the page's review
  embeds. It changes nothing.
- ``users confirm`` takes that token (or one the page signed: tokens are
  bound to the Administrator, not the channel) and records the
  configuration request exactly as the page's **Apply** does. In the
  request's own durable transaction it records ``admin_cmd_users_confirm``
  (whose subject is the automation session) and the page's
  ``user_change_requested``; a high-impact change (granting Administrator,
  or demoting or deleting an Administrator) also leaves a ``policy_change``
  automation notice and observes the ``automation_policy_change`` incident
  (admin-automation specification, "High-impact changes"). A repeated
  confirm returns the original request and records nothing new.

The change document (default, pending Administrator confirmation) is JSON::

    {"users": [{"email": "ann@example.org", "name": "Smith, Ann",
                "role": "staff"},
               {"id": "<saved>", "role": "administrator"},
               {"id": "<saved>", "delete": true}]}

An entry without ``id`` adds a user; ``role`` defaults to Ministry leader
and a missing ``name`` to the first ParishSoft name that lists the address
(the page's default), which the preview shows; the other names found are
only counted (personal data on the command line). A saved user named by
``id`` keeps every field the entry leaves out; ``delete`` removes it. Users
the document does not name stay as they are.
"""

import json
from dataclasses import dataclass

from django.db import DatabaseError

from .admin_changes import InvalidChange
from .admin_reads import ReadModel, Unavailable, _held, _recheck, config_request

TOKEN_LIMIT = 256_000
CHANGES_LIMIT = 256_000


def parse_users(text):
    """Parse a change document's shape; the values are ``plan_changes``' job.

    Refuses (``ValueError``, ``invalid``) anything but an object whose only
    member is a non-empty ``users`` list of objects.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if not isinstance(value, dict) or set(value) != {"users"}:
        raise ValueError("The change document must have only a users list.")
    entries = value["users"]
    if not isinstance(entries, list) or not entries:
        raise ValueError("The change document's users are not valid.")
    if not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("A user in the change document is not valid.")
    return entries


@dataclass(frozen=True)
class UsersPreview(ReadModel):
    """The page's review of a user change, and the token that confirms it.

    ``version`` is the applied configuration's digest the change was made
    against. Each change names the user's ``id``, ``email`` and
    ``operation`` (``add``, ``update`` or ``remove``), its ``name`` and
    ``role`` ``before`` and ``after`` (None when added or removed) and, for
    an added user, how many ``other_names`` ParishSoft offered beyond the one
    used. ``high_impact`` says the change grants, demotes or deletes an
    Administrator. ``preview.token`` is the signed binding ``users confirm``
    takes, valid for fifteen minutes.
    """

    version: str
    changes: list
    high_impact: bool
    preview: dict


@dataclass(frozen=True)
class UsersConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _admit(caller, service):
    """The page's admission of a change, recording activity as its post does.

    A session that ended since the command was admitted is exit 5
    (``session_ended``), not the page's signed-out refusal.
    """
    from .accounts.admin_editing import principal
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.policy import Capability
    from .accounts.sessions import authenticated_admin

    try:
        return principal(caller, service, capability=Capability.MANAGE_USERS)
    except PermissionError:
        if authenticated_admin(caller, store=service.store, read_only=True) is None:
            raise SessionUnusable("session_ended") from None
        raise


def _problem(refused):
    """One ``InvalidChange`` field problem for a ``UserRefused``.

    The field is ``users.<index>.<name>`` for an entry's field, or
    ``users.<index>`` / ``users`` for the entry or document as a whole.
    """
    field = (
        "users"
        if refused.code == "last_administrator"
        else (f"users.{refused.index}" + (f".{refused.field}" if refused.field else ""))
    )
    code = "required" if refused.code.endswith("_required") else "invalid"
    return {"field": field, "code": code, "message": str(refused.message)}


def preview_users(caller, service, *, expected_version, changes):
    """``users preview``: the Users page's review of a change document.

    Admits as the page's Review does, then, in one read-only snapshot, plans
    the change with the page's checks against the applied users. A base
    other than ``expected_version`` is ``stale_version``; a refused change is
    ``invalid`` with each problem at its entry. Rechecks access afterwards.
    Changes nothing.
    """
    from uuid import uuid4

    from parishkit.config import ConfigError

    from .accounts.admin_editing import sign_preview
    from .accounts.policy import Capability
    from .accounts.request_patch import build_candidate
    from .accounts.user_changes import (
        LookupLimited,
        UserRefused,
        clean_email,
        member_names,
        plan_changes,
    )
    from .accounts.user_views import SALT, _users
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit(caller, service)
    entries = parse_users(changes)
    # Each added user without a Name gets the page's ParishSoft default. The
    # lookup definer writes its own audit event and counts against this
    # Administrator's hourly limit, so it runs here, before the read-only
    # snapshot below, under this command's own session.
    found = {}
    for entry in entries:
        if "id" in entry or entry.get("name") is not None:
            continue
        try:
            email = clean_email(entry.get("email"))
        except UserRefused:
            continue  # plan_changes reports it at its entry.
        if email not in found:
            try:
                found[email] = member_names(caller.session.session_key, email)
            except LookupLimited:
                raise Unavailable(
                    "Too many ParishSoft Name lookups this hour; give each new "
                    "user a name, or try again later."
                ) from None

    def step():
        """Plan, validate and sign in one snapshot, as the page's Review does."""
        others = {}

        def default_name(email):
            """The page's ParishSoft default, counting the names not used."""
            names = found.get(email, [])
            others[email] = max(len(names) - 1, 0)
            return names[0] if names else None

        with read_transaction():
            configuration, records = _users(service)
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The users changed since that version.")
            try:
                planned, patch = plan_changes(
                    records, entries, default_name=default_name
                )
            except UserRefused as refused:
                raise InvalidChange([_problem(refused)]) from None
            base = service.store.active()
            if base is None or base.digest != expected_version:
                raise StaleRecordError("The applied configuration changed.")
            try:
                build_candidate(base, patch, candidate_id=uuid4())
            except ConfigError:
                raise InvalidChange(
                    [
                        {
                            "field": "users",
                            "code": "invalid",
                            "message": "This change can't be made.",
                        }
                    ]
                ) from None
            high = any(
                change.grants_administrator or change.removes_administrator
                for change in planned
            )
            token = sign_preview(
                actor=actor,
                configuration=configuration,
                patch=patch,
                salt=SALT,
                extra={"high_impact": high},
            )
        return UsersPreview(
            version=expected_version,
            changes=[
                {
                    "id": change.id,
                    "email": change.email,
                    "operation": change.operation,
                    "before": change.before,
                    "after": change.after,
                    **(
                        {"other_names": others.get(change.email, 0)}
                        if change.operation == "add"
                        else {}
                    ),
                }
                for change in planned
            ],
            high_impact=high,
            preview={"token": token},
        )

    model = _held(step)
    _recheck(caller, service.store, actor, Capability.MANAGE_USERS)
    return model


def confirm_users(caller, service, *, token, context):
    """``users confirm``: the page's **Apply** for a reviewed user change.

    Admits as the page does, then ``confirm_intent`` checks the token
    (expired or out of date is ``stale_version``, altered ``invalid``,
    another Administrator's ``denied``) and records the request with its
    audit events (and, for a high-impact change, the automation notice) in
    its own durable transaction. ``context["committed"]`` and
    ``context["request_id"]`` follow ``schedule confirm``'s rules, so a
    later error is ``outcome_unknown`` naming the request.
    """
    from uuid import UUID

    from django.core import signing

    from .accounts.admin_editing import confirm_intent
    from .accounts.automation_sessions import notify, observe
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.policy import Capability
    from .accounts.user_views import SALT, _audit_change, _scope
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .jobs.operational_content import IncidentKind
    from .observability import _guard_refusal

    actor = _admit(caller, service)
    try:
        key = UUID(signing.loads(token, salt=SALT)["key"])
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        key = None
    if key is not None:
        context["request_id"] = str(policy_operation_id(actor.identity, key))
    created = []
    page_audit = _audit_change(actor)

    def attach(request, extra):
        """Record the command's events with the request it created."""
        record_action(
            Action.ADMIN_CMD_USERS_CONFIRM,
            actor_kind=ActorKind.PORTAL_USER,
            actor_id=actor.identity,
            subject_id=caller.automation_session_id,
            context={"outcome": Outcome.SUCCEEDED},
        )
        page_audit(request, extra)
        if (extra or {}).get("high_impact"):
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
            salt=SALT,
            link=None,
            current_scope=_scope,
            capability=Capability.MANAGE_USERS,
            attach=attach,
        )
    except signing.BadSignature:
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _recheck(caller, service.store, actor, Capability.MANAGE_USERS)
        raise error
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return UsersConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
