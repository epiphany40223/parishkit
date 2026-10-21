"""Create the campaign commands (#1899): ``campaign create-preview``, ``-confirm``.

Create the campaign (``admin:campaign_create``, #142) adds the deployment's
first campaign, and only that one: ``single_campaign.first_campaign_admitted``
refuses it once any campaign row exists. The commands go through the page's
own request-free pieces, so the page and the command line cannot drift: its
admission (``campaign_create_views._admitted``), its form
(``creation_form``), its Review (``creation_review``), its salt (``SALT``)
and its Apply's recheck under the work lock (``_scope``).

- ``campaign create-preview`` admits as the page's Review post does
  (recording activity, so it needs a full-scope session), checks the page's
  admission in the page's work transaction, binds the change document to the
  page's form and runs the page's Review, printing ``preview.token``, the
  same signed binding the dialog's Apply posts. It changes nothing and
  records no event, as the page's Review records none.
- ``campaign create-confirm`` takes that token (or one the page signed) and records
  the configuration request exactly as the dialog's Apply does, through
  ``admin_editing.confirm_intent`` with ``first_campaign=True``. In the
  request's own durable transaction it also records one
  ``admin_cmd_campaign_create`` event. A repeat returns the original request
  and records nothing new, even once the campaign exists.

Neither is privileged or prompts, as the page's Apply is the reviewed token.
The change document is ``campaign preview``'s (``admin_campaign``); a field
it leaves out takes the value the page's form opens with.
"""

from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_campaign import field_errors, form_data, parse_settings
from .admin_campaign import saved_settings as form_settings
from .admin_changes import _admit_change, _ended_or_raise
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request


@dataclass(frozen=True)
class CampaignCreatePreview(ReadModel):
    """The page's review of a new campaign, and its token.

    ``settings`` is every form field as posted (the document over the form's
    starting values), in ``campaign show``'s shape. ``content`` is how many
    default pages and emails the campaign starts with. ``preview.token`` is
    the signed binding ``campaign create-confirm`` takes, valid for fifteen minutes.
    """

    version: str
    settings: dict
    content: int
    preview: dict


@dataclass(frozen=True)
class CampaignCreate(ReadModel):
    """The configuration request a creation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated create), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _refused():
    """The refusal when the first campaign cannot be created now.

    The page answers 409 (``first_campaign_refused``), which the command line
    would read as ``stale_version``; no newer version fixes it, so it is
    ``not_available``, whose document carries only that code's closed text.
    """
    return NotAvailable("The campaign can't be created right now.")


def _scope(service):
    """The page's Apply recheck under the work lock, refused as ``not_available``.

    This wraps the page's own ``_scope``, so the two cannot drift: the
    page's refusal (a ``UserFacingStale``, which the page shows as a 409)
    becomes ``_refused()``.
    """
    from .accounts.campaign_create_views import _scope as page_scope
    from .web.refusals import UserFacingStale

    try:
        return page_scope(service)
    except UserFacingStale:
        raise _refused() from None


def preview_create(caller, service, *, expected_version, changes):
    """``campaign create-preview``: Create the campaign's Review.

    Admits as the page's Review post does, then, in the page's work
    transaction, checks the page's admission (``not_available`` when it no
    longer or not yet holds), binds the document over the form's starting
    values to the page's form and runs the page's ``creation_review``. The
    form's field errors and a candidate the configuration refuses are
    ``invalid``; a base other than ``expected_version`` is ``stale_version``
    (the page's ``stale_page``). Rechecks access afterwards. Changes nothing.
    """
    from .accounts.campaign_create_views import (
        _admitted,
        creation_form,
        creation_review,
    )
    from .accounts.campaign_views import _state
    from .accounts.policy import Capability
    from .admin_changes import InvalidChange
    from .campaigns.work_locks import work_transaction

    actor = _admit_change(caller, service)
    fields = parse_settings(changes)

    def step():
        """Admit, bind, review and sign in one work transaction, as the page."""
        with work_transaction():
            state = _state(service)
            if not _admitted(state):
                raise _refused()
            configuration, source = state[0], state[2]
            start = creation_form(configuration, source)
            # Every field is open on a new campaign, so the post is the
            # form's starting values with the document over them.
            post = form_data(start, set(start.fields), fields, expected_version)
            form = creation_form(configuration, source, post)
            review = creation_review(service, actor, state, form)
            if review is None:
                raise InvalidChange(field_errors(form))
        return CampaignCreatePreview(
            version=expected_version,
            settings=form_settings(start) | fields,
            content=review["content"],
            preview={"token": review["preview"]},
        )

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm_create(caller, service, *, token, context):
    """``campaign create-confirm``: the review dialog's **Apply** for a new campaign.

    Admits as the page does, then ``confirm_intent`` checks the token with the
    page's salt (expired, or reviewed before the settings changed, is
    ``stale_version``; altered or another page's ``invalid``; another
    Administrator's ``denied``), rechecks admission under the work lock
    (``not_available``) and records the request and
    ``admin_cmd_campaign_create`` in its own durable transaction. A restore
    under review or an activating change is ``unavailable`` (``_held``).
    ``context["committed"]`` and ``context["request_id"]`` follow ``campaign
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.campaign_create_views import SALT
    from .accounts.configuration_requests import policy_operation_id
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
        """Record the command's event in the request's own transaction."""
        record_action(
            Action.ADMIN_CMD_CAMPAIGN_CREATE,
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
            first_campaign=True,
        )
    except signing.BadSignature:
        # Altered, or signed by another page (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return CampaignCreate(
        created=bool(created), request=config_request(receipt).to_document()
    )
