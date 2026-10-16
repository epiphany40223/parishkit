"""No-reminders WorkGroup commands (ADM-11 PR 10): show, preview and confirm.

The No-reminders WorkGroup page (``admin:reminder_workgroup``, #861) names
the one ParishSoft Family WorkGroup whose Families get no Reminders in the
current campaign. The commands go through the page's own request-free
pieces, so the page and the command line cannot drift: the applied
configuration (``admin_editing.editable_configuration``), the page's
current-campaign check (``reminder_workgroup_views._campaign``), its form
(``WorkGroupForm``), its review (``reminder_workgroup_views.review_change``)
and what the newest refresh found (``source.workgroups.current_evidence``).

- ``workgroup show`` admits passively with the page's ``CONFIGURE``
  capability (so Administrators only, and a read-only session reads),
  reads in one read transaction, rechecks the session and records nothing,
  as the page's GET records nothing. It prints the page's **Now** panel as
  a closed ``state`` and a Family count, never a Family's DUID or name.
- ``workgroup preview`` admits as the page's Review post does (recording
  activity, so it needs a full-scope session), binds the name to the
  page's form and builds the page's review in one read snapshot, printing
  ``preview.token``, the same signed binding the page's Apply posts. It
  changes nothing and records no event, as the page's Review records none.
- ``workgroup confirm`` takes that token (or one the page signed) and
  records the configuration request exactly as the page's Apply does,
  through ``admin_editing.confirm_intent`` with the page's salt (bound to
  the current campaign), with one ``admin_cmd_workgroup_confirm`` event in
  the request's own durable transaction. A repeat returns the original
  request and records nothing new.

None is fresh-gated or prompts, as the page asks for no recent sign-in and
shows no confirmation dialog: the reviewed token is the confirmation. Once
the change is applied, the old WorkGroup stops applying at once; a new name
applies only from the next ParishSoft refresh that reads it, so until then
no Family is excluded (``source.workgroups.current_evidence``).
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel
from .admin_settings import _read


@dataclass(frozen=True)
class WorkGroupShow(ReadModel):
    """The current campaign's No-reminders WorkGroup, as the page draws it.

    ``name`` is the configured WorkGroup name, or None when the setting is
    off. ``state`` is the page's **Now** panel: ``off`` (no WorkGroup),
    ``pending`` (the next refresh reads it), ``missing`` (the newest
    refresh found no Family WorkGroup of that name) or ``found``;
    ``families`` is the number of Families the refresh found in it when
    ``found``, else None. ``version`` is what ``workgroup preview
    --expected-version`` takes.
    """

    version: str
    campaign_id: UUID
    name: str | None
    state: str
    families: int | None


@dataclass(frozen=True)
class WorkGroupPreview(ReadModel):
    """The page's review of a WorkGroup change, and its token.

    ``before`` and ``after`` are the WorkGroup names (None for off).
    ``preview.token`` is the signed binding ``workgroup confirm`` takes,
    valid for fifteen minutes.
    """

    version: str
    campaign_id: UUID
    before: str | None
    after: str | None
    preview: dict


@dataclass(frozen=True)
class WorkGroupConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def now_state(name, evidence):
    """``(state, families)``: the page's **Now** panel, as closed values.

    The same branches as the page's template, in its order: no name is
    ``off``; no refresh evidence for that name yet is ``pending``; a refresh
    that found no such WorkGroup is ``missing``; otherwise ``found`` with
    the number of member Families.
    """
    if not name:
        return "off", None
    if not evidence:
        return "pending", None
    if not evidence["found"]:
        return "missing", None
    return "found", len(evidence["family_duids"])


def _current_campaign(configuration):
    """The page's current-campaign check; none is ``not_available``."""
    from .accounts.reminder_workgroup_views import _campaign

    try:
        return _campaign(configuration, configuration.current_campaign_id)
    except LookupError:
        raise NotAvailable("There is no current campaign.") from None


def read_workgroup(caller, service):
    """``workgroup show``: the current campaign's No-reminders WorkGroup."""
    from .accounts.admin_editing import editable_configuration
    from .source.workgroups import current_evidence

    def read():
        """The page's campaign check and its Now panel."""
        configuration = editable_configuration(service)
        campaign = _current_campaign(configuration)
        name, evidence = current_evidence(campaign.active_configuration.values)
        state, families = now_state(name, evidence)
        return WorkGroupShow(
            version=configuration.active_configuration.digest,
            campaign_id=campaign.pk,
            name=name,
            state=state,
            families=families,
        )

    return _read(caller, service, read)


def preview_workgroup(caller, service, *, name, expected_version):
    """``workgroup preview``: the page's review of setting the WorkGroup.

    ``name`` is the new name, or None (``--clear``) to turn the setting off.
    A name that is blank once trimmed is refused (``invalid`` at ``name``)
    before anything else: the page's form would read it as off, and an
    unset shell variable must never silently clear the exclusion; turning
    it off takes ``--clear``. Admits as the page's Review post does, then,
    in one read snapshot, binds the name to
    the page's form (a name it refuses is ``invalid`` at ``name``), checks
    the version (another is ``stale_version``) and builds the page's own
    review: no change is ``invalid``, as the page refuses it, and a
    candidate the configuration refuses is ``invalid``. Rechecks access
    afterwards. Changes nothing.
    """
    from parishkit.config import ConfigError

    from .accounts.admin_editing import editable_configuration
    from .accounts.policy import Capability
    from .accounts.reminder_workgroup_views import WorkGroupForm, review_change
    from .admin_changes import InvalidChange, _admit_change, _ended_or_raise, _problem
    from .admin_reads import _held, _recheck
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    if name is not None and not name.strip():
        raise InvalidChange(
            [
                {
                    "field": "name",
                    "code": "required",
                    "message": "Give a WorkGroup name; use --clear to name none.",
                }
            ]
        )
    actor = _admit_change(caller, service)

    def step():
        """Validate, check the version and review in one snapshot."""
        with read_transaction():
            configuration = editable_configuration(service)
            campaign = _current_campaign(configuration)
            form = WorkGroupForm({"name": name or "", "base_digest": expected_version})
            if not form.is_valid():
                raise InvalidChange(
                    [
                        _problem("name", item)
                        for item in form.errors.get_json_data().get("name", [])
                    ]
                )
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            try:
                review = review_change(
                    service, actor, configuration, campaign, form.cleaned_data["name"]
                )
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the page's
                # review would.
                raise ValueError("The configuration refuses this change.") from None
            if review is None:
                raise InvalidChange(
                    [
                        {
                            "field": "name",
                            "code": "invalid",
                            "message": "The No-reminders WorkGroup has not changed.",
                        }
                    ]
                )
            return WorkGroupPreview(
                version=expected_version,
                campaign_id=campaign.pk,
                before=review["before"],
                after=review["after"],
                preview={"token": review["preview"]},
            )

    try:
        model = _held(step)
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm_workgroup(caller, service, *, token, context):
    """``workgroup confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for the current campaign (expired or out of date is
    ``stale_version``; altered, another campaign's or another page's
    ``invalid``; another Administrator's ``denied``) and records the request
    with ``admin_cmd_workgroup_confirm`` in its own durable transaction. The
    configuration must be editable first (``_held``), so a restore under
    review or an activating change is ``unavailable``.
    ``context["committed"]`` and ``context["request_id"]`` follow ``parish
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing
    from django.db import DatabaseError

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.reminder_workgroup_views import SALT
    from .admin_changes import _admit_change, _ended_or_raise
    from .admin_reads import _held, config_request
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    configuration = _held(lambda: editable_configuration(service))
    if configuration.current_campaign_id is None:
        raise NotAvailable("There is no current campaign.")
    # The page posts to its campaign's own address and signs with that
    # campaign in the salt; the command line acts on the current campaign.
    salt = SALT + str(configuration.current_campaign_id)
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
        """Record the command's event with the request it created."""
        record_action(
            Action.ADMIN_CMD_WORKGROUP_CONFIRM,
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
            salt=salt,
            link=None,
            # The page's own scope: only the editable configuration.
            current_scope=lambda service: (editable_configuration(service), None),
            attach=attach,
        )
    except signing.BadSignature:
        # Altered, another campaign's or another page's (an expired one is
        # stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return WorkGroupConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
