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

``users import-leaders`` (#952 slice 6) is the page's ParishSoft Ministry
leaders table and its import: ``--preview`` prints the leaders without a
user and the review of importing everyone (``--all``) or the chosen
addresses (``--email``), and ``--confirm`` applies that review exactly as
the page's Apply does, through ``confirm_users``.

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

The commands act as the Administrator who approved the automation session,
so the page's own-Administrator guard applies to them too: no entry may
demote or delete that Administrator's own user (Administrator decision,
2026-10-11). Another Administrator makes that change, on the page or with
their own session.
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


def _lookup_refused(caller, service):
    """Report the Name lookup definer's session refusal (SQLSTATE 42501).

    The definer refuses a session that is no longer live (revoked, past its
    deadline or idle limit, or begun before a recovery) or whose user is no
    longer an Administrator: an ended session, exit 5 (``session_ended``),
    as admission reports it. Should the session still pass Python's own
    check (the two clocks a moment apart), the preview is ``unavailable``
    (exit 3) and safe to retry. Either way nothing was looked up or audited.
    """
    from .accounts.automation_sessions import SessionUnusable
    from .accounts.sessions import authenticated_admin

    if authenticated_admin(caller, store=service.store, read_only=True) is None:
        raise SessionUnusable("session_ended") from None
    raise Unavailable("The ParishSoft Names can't be looked up now; try again.")


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
        LOOKUP_REFUSED,
        LookupLimited,
        UserRefused,
        acting_email,
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
            except DatabaseError as error:
                if getattr(error.__cause__, "sqlstate", None) != LOOKUP_REFUSED:
                    raise
                _lookup_refused(caller, service)

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
                    records,
                    entries,
                    default_name=default_name,
                    own_email=acting_email(actor.identity),
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


def confirm_users(
    caller, service, *, token, context, command_action="ADMIN_CMD_USERS_CONFIRM"
):
    """``users confirm``: the page's **Apply** for a reviewed user change.

    Admits as the page does, then ``confirm_intent`` checks the token
    (expired or out of date is ``stale_version``, altered ``invalid``,
    another Administrator's ``denied``) and records the request with its
    audit events (and, for a high-impact change, the automation notice) in
    its own durable transaction. ``command_action`` names the ``Action`` of
    the command's own event (``users import-leaders --confirm`` passes its
    own). ``context["committed"]`` and ``context["request_id"]`` follow
    ``schedule confirm``'s rules, so a later error is ``outcome_unknown``
    naming the request.
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
            getattr(Action, command_action),
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


@dataclass(frozen=True)
class LeaderImport(ReadModel):
    """``users import-leaders``: the leaders table, and its import's review.

    ``--preview`` fills ``version`` (the applied configuration's digest),
    ``leaders`` (each ParishSoft Ministry leader without a user, as the
    page's second table lists them: ``email``, ``name``, ``member_duid`` and
    the ``ministries`` they lead, by name), ``unreachable`` (leaders with no
    usable email, only counted), and the import the chosen leaders would
    make: ``added`` (each new user's ``id``, ``email``, ``name``,
    ``member_duid`` and ``role``, always ``ministry_leader``), ``skipped``
    (``email`` and ``reason``: ``user`` or ``not_listed``), ``remaining``
    (chosen leaders past one change's limit) and ``preview.token``, which
    ``--confirm`` takes (null when nobody is chosen or left to import).
    ``--confirm`` fills ``created`` and ``request`` as ``users confirm``
    does; the other fields are then null.
    """

    version: str | None = None
    leaders: list | None = None
    unreachable: int | None = None
    added: list | None = None
    skipped: list | None = None
    remaining: int | None = None
    preview: dict | None = None
    created: bool | None = None
    request: dict | None = None


def _leaders(caller, service):
    """The leaders without a user, read under this command's own session.

    The definer records its own audit event and checks the command session
    itself; a session it refuses is reported as ``_lookup_refused`` says.
    """
    from .accounts.leader_import import LeadersRefused, read_leaders

    try:
        return read_leaders(caller.session.session_key)
    except LeadersRefused:
        _lookup_refused(caller, service)


def preview_import(caller, service, *, everyone, emails):
    """``users import-leaders --preview``: the page's leaders table and review.

    Admits as the page's Import does, reads the leaders, then, in one
    read-only snapshot, plans importing everyone (``everyone``), the chosen
    ``emails``, or nobody (neither: the list alone) with the page's code,
    validates the resulting configuration and signs the review. Rechecks
    access afterwards. Records only the definer's own audit event.
    """
    from uuid import uuid4

    from parishkit.config import ConfigError

    from .accounts.admin_editing import sign_preview
    from .accounts.leader_import import plan_import, table_rows
    from .accounts.policy import Capability
    from .accounts.request_patch import build_candidate
    from .accounts.user_changes import UserRefused, clean_email
    from .accounts.user_views import SALT, _users
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    actor = _admit(caller, service)
    chosen = None
    if not everyone:
        try:
            chosen = [clean_email(email) for email in emails]
        except UserRefused as refused:
            raise InvalidChange([_problem(refused)]) from None
    leaders = _leaders(caller, service)

    def step():
        """Plan, validate and sign in one snapshot, as the page's review does."""
        with read_transaction():
            configuration, records = _users(service)
            try:
                plan = plan_import(records, leaders, chosen)
            except UserRefused as refused:
                raise InvalidChange([_problem(refused)]) from None
            token = None
            if plan.patch:
                base = service.store.active()
                if (
                    base is None
                    or base.digest != configuration.active_configuration.digest
                ):
                    raise StaleRecordError("The applied configuration changed.")
                try:
                    build_candidate(base, plan.patch, candidate_id=uuid4())
                except ConfigError:
                    raise InvalidChange(
                        [
                            {
                                "field": "users",
                                "code": "invalid",
                                "message": "This import can't be made.",
                            }
                        ]
                    ) from None
                token = sign_preview(
                    actor=actor,
                    configuration=configuration,
                    patch=plan.patch,
                    salt=SALT,
                    extra={"leader_import": True},
                )
            rows = table_rows(leaders)
        return LeaderImport(
            version=configuration.active_configuration.digest,
            leaders=[
                {
                    "email": row["email"],
                    "name": row["name"],
                    "member_duid": row["member_duid"],
                    "ministries": row["ministry_names"],
                }
                for row in rows
            ],
            unreachable=leaders.unreachable,
            added=[
                {
                    "id": change.id,
                    "email": change.email,
                    "name": change.after["name"],
                    "member_duid": row["member_duid"],
                    "role": change.after["role"],
                }
                for change, row in zip(plan.changes, plan.added, strict=True)
            ],
            skipped=plan.skipped,
            remaining=plan.remaining,
            preview={"token": token},
        )

    model = _held(step)
    _recheck(caller, service.store, actor, Capability.MANAGE_USERS)
    return model


def confirm_import(caller, service, *, token, context):
    """``users import-leaders --confirm``: the import review's **Apply**.

    Refuses (``invalid``) a token that is not an import review, so a
    ``users preview`` token cannot be applied here. Otherwise it records the
    request as the page's Apply does, through ``confirm_users``' path, with
    ``admin_cmd_users_import_leaders`` and the page's import audit events
    (``user_leaders_import_requested`` and one ``user_leader_imported`` per
    new user) in the request's own durable transaction. An import grants
    only Ministry leader, so it is never high impact.
    """
    from django.core import signing

    from .accounts.user_views import SALT

    try:
        extra = signing.loads(token, salt=SALT).get("extra") or {}
    except (signing.BadSignature, AttributeError, TypeError, ValueError):
        raise ValueError("The preview token is not valid.") from None
    if extra.get("leader_import") is not True:
        raise ValueError("The preview token is not an import of leaders.")
    confirmed = confirm_users(
        caller,
        service,
        token=token,
        context=context,
        command_action="ADMIN_CMD_USERS_IMPORT_LEADERS",
    )
    return LeaderImport(created=confirmed.created, request=confirmed.request)
