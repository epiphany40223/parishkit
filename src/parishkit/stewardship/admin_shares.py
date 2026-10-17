"""Share options commands (ADM-11 PR 10): ``campaign shares-preview`` and
``campaign shares-confirm``.

Share options (``admin:share_settings``) edits one campaign's ordered list
of share options while it is an unlocked Testing draft with the financial
module. ``campaign shares`` (in ``admin_settings``) is its read; these two
commands are its Review and Apply, through the page's own request-free
pieces, so the page and the command line cannot drift: the pinned state
and target check (``campaign_views._state`` and ``_target``), the editor's
editability refusal (``option_editing._unavailable``), its formset
(``share_forms.ShareOptions``), its review (``option_editing.review_options``)
and its Apply (``admin_editing.confirm_intent`` with the page's salt and
``campaign_views._scope``).

- ``campaign shares-preview`` admits as the page's Review post does
  (recording activity, so it needs a full-scope session), binds the change
  document to the page's formset in the page's work transaction and runs
  the page's review, printing ``preview.token``, the same signed binding
  the page's Apply posts. It changes nothing and records no event, as the
  page's Review records none.
- ``campaign shares-confirm`` takes that token (or one the page signed)
  and records the configuration request exactly as the page's Apply does,
  with one ``admin_cmd_campaign_shares_confirm`` event in the request's own
  durable transaction. A repeat returns the original request and records
  nothing new.

Neither is privileged or prompts, as its Apply is the reviewed token. Both
default to the current campaign, as ``campaign shares`` does.

The change document (default, pending Administrator confirmation) names
the whole new list and every saved option it deletes::

    {"options": [{"id": "<saved id>", "name": "Online giving",
                  "short_name": "Online", "free_text": false},
                 {"name": "A new option", "free_text": true}],
     "delete": ["<saved id>"]}

``options`` is the kept and new options in their new order (a new option
has no ``id``; ``name`` is the page's label; ``short_name`` its optional
short label, blank by default; ``free_text`` defaults to false) and
``delete`` the saved options removed, as the page's Delete boxes. Every
saved option must be in exactly one of the two, the page's own rule
("Every saved option must be retained or explicitly deleted."), so leaving
one out never deletes it.
"""

import json
from dataclasses import dataclass
from uuid import UUID

from django.db import DatabaseError

from .admin_changes import (
    FieldProblem,
    InvalidChange,
    _admit_change,
    _ended_or_raise,
    _problem,
    unexpected_problem,
)
from .admin_reads import NotAvailable, ReadModel, _held, _recheck, config_request

CHANGES_LIMIT = 512_000
# The page's own bound on posted rows (``share_forms.share_action`` and the
# formset's ``absolute_max``).
MAX_ROWS = 101
OPTION_KEYS = frozenset({"id", "name", "short_name", "free_text"})


@dataclass(frozen=True)
class SharesPreview(ReadModel):
    """The page's review of a Share options change, and its token.

    ``changes`` is the page's compact review, one line per change
    (``label``, ``before``, ``after``; ``before`` is null for an added or
    removed option). ``options`` is the list the change saves, in order,
    each option's ``id``, ``name``, ``short_name`` (empty when unset) and
    ``free_text``; a new option's id is the one the change gives it.
    ``preview.token`` is the signed binding ``campaign shares-confirm``
    takes, valid for fifteen minutes.
    """

    version: str
    campaign_id: UUID
    changes: list
    options: list
    preview: dict


@dataclass(frozen=True)
class SharesConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def _shape(message, field="options"):
    """One ``invalid`` problem of the document's shape, at ``field``."""
    return FieldProblem(
        {"field": field, "code": "invalid", "message": message}, logged="options"
    )


def _identifier(value):
    """A share option id in its canonical spelling, or None if it is not one.

    The page's formset reads ids as UUIDs; refusing any other value here
    keeps a mistyped id from being quietly dropped with its deleted row.
    """
    try:
        return str(UUID(value)) if isinstance(value, str) else None
    except ValueError:
        return None


def parse_changes(text):
    """Parse a change document's shape; the values are the page formset's job.

    Refuses (``ValueError``, ``invalid``) anything but an object with an
    ``options`` list and, optionally, a ``delete`` list. An option that is
    not an object, a member it does not have, or a member of the wrong JSON
    type is ``InvalidChange`` at ``options.<n>`` or ``options.<n>.<name>``;
    an id that is not a UUID at ``options.<n>.id`` or ``delete``. Returns
    ``(options, delete)``, the ids in their canonical spelling.
    """
    if len(text) > CHANGES_LIMIT:
        raise ValueError("The change document is too long.")
    try:
        value = json.loads(text)
    except ValueError:
        raise ValueError("The change document is not JSON.") from None
    if (
        not isinstance(value, dict)
        or "options" not in value
        or set(value) - {"options", "delete"}
    ):
        raise ValueError(
            "The change document must have an options list and may have a delete list."
        )
    options, delete = value["options"], value.get("delete", [])
    if not isinstance(options, list) or not isinstance(delete, list):
        raise ValueError("The change document's options and delete must be lists.")
    problems = []
    for index, option in enumerate(options):
        at = f"options.{index}"
        if not isinstance(option, dict):
            problems.append(_shape("Give an object.", at))
            continue
        for name in sorted(set(option) - OPTION_KEYS):
            # A key the caller sent: counted in the refusal log, never named.
            problems.append(
                unexpected_problem(f"{at}.{name}", "This is not a share option field.")
            )
        if not isinstance(option.get("name"), str):
            problems.append(
                _shape("Give the option's label as a string.", f"{at}.name")
            )
        if not isinstance(option.get("short_name", ""), str):
            problems.append(
                _shape("Give the option's short label as a string.", f"{at}.short_name")
            )
        if not isinstance(option.get("free_text", False), bool):
            problems.append(_shape("Give true or false.", f"{at}.free_text"))
        if "id" in option:
            option["id"] = _identifier(option["id"])
            if option["id"] is None:
                problems.append(_shape("Give a saved option's id.", f"{at}.id"))
    delete = [_identifier(item) for item in delete]
    if None in delete:
        problems.append(_shape("Give each saved option's id.", "delete"))
    if len(options) + len(delete) > MAX_ROWS:
        problems.append(_shape("Give at most 100 share options."))
    if problems:
        raise InvalidChange(problems)
    return options, delete


def formset_data(previous, options, delete, expected_version):
    """The page's Review post for the document, as the page's form sends it.

    One row per document option, in its order (``ORDER`` its position), then
    one ticked Delete row per deleted id, with the saved values the page
    shows greyed. ``INITIAL_FORMS`` is the number of saved options, as the
    page draws them, so the formset's own checks apply unchanged: an id not
    saved, or given twice, is its "identities have changed" refusal, and a
    saved option in neither list is its "retained or explicitly deleted"
    one. A check box is sent only when ticked, as the browser sends it.
    """
    saved = {row["id"]: row for row in previous}
    rows = [
        {
            "id": option.get("id", ""),
            "label": option["name"],
            "short_label": option.get("short_name", ""),
            "free_text": option.get("free_text", False),
            "ORDER": str(position),
        }
        for position, option in enumerate(options, start=1)
    ]
    for identifier in delete:
        row = saved.get(identifier, {})
        rows.append(
            {
                "id": identifier,
                "label": row.get("label", ""),
                "short_label": row.get("short_label", ""),
                "free_text": row.get("free_text", False),
                "DELETE": True,
            }
        )
    post = {
        "base_digest": expected_version,
        "options-TOTAL_FORMS": str(len(rows)),
        "options-INITIAL_FORMS": str(len(previous)),
        "options-MIN_NUM_FORMS": "0",
        "options-MAX_NUM_FORMS": "100",
    }
    for index, row in enumerate(rows):
        for name, value in row.items():
            if value is True:
                post[f"options-{index}-{name}"] = "on"
            elif value is not False:
                post[f"options-{index}-{name}"] = value
    return post


def field_errors(formset, count):
    """Every problem the page's formset found, with the page's messages.

    A document option's problem is at ``options.<n>.<name>`` (the label is
    ``name``, as ``campaign shares`` prints it); the formset's own (ids,
    missing rows, too many options) at ``options``. A deleted row's fields
    are not checked, as on the page.
    """
    problems = [
        _problem("options", item, "options")
        for item in formset.non_form_errors().get_json_data()
    ]
    for index, form in enumerate(formset.forms[:count]):
        for name, messages in form.errors.get_json_data().items():
            shown = {"label": "name", "short_label": "short_name", "__all__": None}.get(
                name, name
            )
            field = f"options.{index}" + (f".{shown}" if shown else "")
            problems += [
                _problem(field, item, f"options-{shown or 'row'}") for item in messages
            ]
    return problems


def _campaign_id(configuration, campaign_id):
    """The campaign the command edits: the given one, else the current one."""
    target = campaign_id or configuration.current_campaign_id
    if target is None:
        raise NotAvailable("There is no current campaign.")
    return target


def preview_shares(caller, service, campaign_id, *, expected_version, changes):
    """``campaign shares-preview``: Share options' review of a change.

    Admits as the page's Review post does, parses the document, then, in
    the page's work transaction: the page's target and editability checks
    (an unknown campaign is ``not_available``; a campaign the page does not
    edit now is the page's own refusal, ``stale_version``), the version
    (another is ``stale_version``), the page's formset (its problems are
    ``invalid``) and the page's review (no change, or a candidate the
    configuration refuses, is ``invalid``). Rechecks access afterwards.
    Changes nothing.
    """
    from parishkit.config import ConfigError

    from .accounts.campaign_views import _state, _target
    from .accounts.option_editing import _unavailable, review_options
    from .accounts.policy import Capability
    from .accounts.share_forms import ShareOptions
    from .accounts.share_views import EDITOR
    from .campaigns.work_locks import work_transaction
    from .storage import StaleRecordError
    from .web.refusals import UserFacingError

    actor = _admit_change(caller, service)
    options, delete = parse_changes(changes)

    def step():
        """Check, bind, review and sign in one work transaction, as the page."""
        with work_transaction():
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            target = _campaign_id(configuration, campaign_id)
            try:
                campaign, editable = _target(configuration, campaigns, held, target)
            except KeyError:
                # A KeyError is a bug, never "no such campaign": internal.
                raise
            except LookupError:
                raise NotAvailable("No such campaign.") from None
            if (
                not editable
                or EDITOR.module not in campaign.active_configuration.values["modules"]
            ):
                raise _unavailable(EDITOR, configuration, campaign)
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            previous = EDITOR.current(campaign)
            formset = ShareOptions(
                formset_data(previous, options, delete, expected_version),
                prefix="options",
                previous=previous,
            )
            if not formset.is_valid():
                raise InvalidChange(field_errors(formset, len(options)))
            salt = f"{EDITOR.salt}:{campaign.pk}"
            try:
                review = review_options(
                    EDITOR, service, actor, state, campaign, formset, salt
                )
            except UserFacingError as error:
                refusal = error.refusal
                text = " ".join(
                    str(item) for item in (refusal.message, refusal.fix) if item
                )
                raise InvalidChange([_shape(text)]) from None
            except ConfigError:
                # Raised past _held, which would read it as unavailable: the
                # configuration refuses the resulting document, as the
                # page's review would.
                raise ValueError("The configuration refuses this change.") from None
        return SharesPreview(
            version=expected_version,
            campaign_id=campaign.pk,
            changes=[
                {
                    "label": str(line["label"]),
                    "before": None if line["before"] is None else str(line["before"]),
                    "after": str(line["after"]),
                }
                for line in review["changes"]
            ],
            options=[
                {
                    "id": option["id"],
                    "name": option["label"],
                    "short_name": option.get("short_label", ""),
                    "free_text": option["free_text"],
                }
                for option in review["options"]
            ],
            preview={"token": review["preview"]},
        )

    try:
        model = _held(step)
    except (NotAvailable, PermissionError) as error:
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm_shares(caller, service, campaign_id, *, token, context):
    """``campaign shares-confirm``: the page's **Apply** for a reviewed change.

    Admits as the page does, then ``confirm_intent`` checks the token with
    the page's salt for the campaign (the given one, else the current one)
    and the page's scope (``campaign_views._scope``): expired or out of date
    is ``stale_version``; altered, another campaign's or another page's
    ``invalid``; another Administrator's ``denied``. It records the request
    with ``admin_cmd_campaign_shares_confirm`` in its own durable
    transaction. ``context["committed"]`` and ``context["request_id"]``
    follow ``parish confirm``'s rules, so a later error is
    ``outcome_unknown`` naming the request.
    """
    from django.core import signing

    from .accounts.admin_editing import confirm_intent, editable_configuration
    from .accounts.campaign_views import _scope
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.share_views import EDITOR
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    actor = _admit_change(caller, service)
    configuration = _held(lambda: editable_configuration(service))
    try:
        target = _campaign_id(configuration, campaign_id)
    except NotAvailable as error:
        # An ended session is exit 5, not this refusal.
        _ended_or_raise(caller, service, actor, error)
    # The page posts to its campaign's own address and signs with that
    # campaign in the salt.
    salt = f"{EDITOR.salt}:{target}"
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
            Action.ADMIN_CMD_CAMPAIGN_SHARES_CONFIRM,
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
            current_scope=_scope,
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
    return SharesConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
