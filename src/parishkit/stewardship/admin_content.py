"""``content list`` and ``content history``: the content reads (ADM-11).

``content list`` is the Pages and emails page (``admin:content_catalog``)
through the page's own pieces: its campaign check (``content_views._campaign``
over ``campaign_views._state``: the current campaign only, and not while
background work holds it) and its row builder (``catalog_entries``: each
page's and email's text state, re-save notice, the schedules that send each
email and whether it can be deleted), so the page and the command line
cannot drift.

``content history`` is the Content history page (``admin:content_history``
and ``admin:content_history_revision``) through the page's own read
(``content_history.retained_content``): the current campaign's retained
pages and emails and, with ``--revision``, one revision's fictional sample,
as the page shows it.

Both admit passively with the pages' ``CONFIGURE`` capability (so
Administrators only, and a read-only session reads), read in one read
transaction, recheck the session before reporting, and record nothing, as
the pages' GETs record nothing. Content is the campaign's own text, never a
Family's; a sample's Family values are the page's fictional ones.

``content show`` is the content editor (``admin:content_edit`` and
``admin:content_revision``) as it opens, through the page's own lookup
(``content_views.editor_content``): one page's or email's saved text, its
text state and re-save notice, or with ``--default`` the slot's default
text as an editor save would store it (the page's "?start=default", which
only pre-fills the editor). It admits, reads and rechecks as ``content
list`` does, and records nothing, as the editor's GET records nothing.

``content preview`` and ``content confirm`` are the email tables' **Delete**
(``admin:content_delete``, #879) split into a preview verb and a confirm
verb, as ``schedule preview`` and ``schedule confirm`` split a schedule's
Delete, plus the editors' live plain-text preview
(``admin:content_plain_text``):

- ``content preview --delete ID ... --expected-version V`` admits as the
  page's Delete post does (recording activity, so it needs a full-scope
  session) and builds and signs the page's own removal
  (``content_delete_views.review_deletion``) in the page's work
  transaction, printing the emails it removes and ``preview.token``. It
  changes nothing and records no event.
- ``content preview --html HTML`` runs the page's own conversion
  (``content_views.plain_text``): the sanitized HTML, its plain text and
  the markup the sanitizer drops. It reads and records nothing.
- ``content confirm --token T`` records the configuration request exactly
  as the page's Delete does, through ``admin_editing.confirm_intent`` with
  the page's delete salt and scope, with one ``admin_cmd_content_confirm``
  event in the request's own durable transaction. A repeat returns the
  original request and records nothing.

Neither is fresh-gated or prompts: the page asks for no recent sign-in, and
the reviewed token stands in for the page's confirmation dialog, as for
``schedule confirm``. Each checks the role first (#1341), before any input
is read. The editor's own Review and Apply are still owed.
"""

from dataclasses import dataclass
from uuid import UUID

from .admin_reads import NotAvailable, ReadModel, _admit, _held, _recheck


@dataclass(frozen=True)
class ContentList(ReadModel):
    """The Pages and emails list of the current campaign.

    ``version`` is the applied configuration's version (its digest), the
    base a content change is previewed against, as ``schedule show``'s.
    ``pages`` lists each Family page (``slot``, ``name``, ``state``,
    ``resave``); ``emails`` each mail type (``slot``, ``name``,
    ``scheduled``, ``singleton``, ``revisions``), in the page's order.
    """

    campaign_id: UUID
    version: str
    pages: list
    emails: list


@dataclass(frozen=True)
class ContentHistory(ReadModel):
    """The retained pages and emails of the current campaign.

    ``configuration_id`` is the campaign's retained configuration version;
    ``entries`` each retained page and email (``id``, ``kind``, ``slot``,
    ``name``, ``subject``) in the page's order; ``revision`` the chosen
    revision's fictional sample (``id``, ``kind``, ``slot``, ``name`` and
    ``sample``: ``subject``, ``html``, ``text``), or None without
    ``--revision``.
    """

    campaign_id: UUID
    state: str
    configuration_id: UUID
    entries: list
    revision: dict | None


@dataclass(frozen=True)
class ContentShow(ReadModel):
    """One page's or email's text as the content editor opens it.

    ``version`` is the applied configuration's version, as ``content
    list``'s; ``name`` the page's label; ``revision_id`` the saved record
    shown, or None; ``state`` and ``resave`` the saved text's, as ``content
    list``. ``from_default`` says ``content`` is the slot's default text
    (``--default``); ``content`` is None for an empty slot, else
    ``subject``, ``html``, ``text``, ``generate_text`` and
    ``show_banner`` (None where the editor has no such field).
    """

    campaign_id: UUID
    version: str
    kind: str
    slot: str
    name: str
    revision_id: str | None
    state: str
    resave: dict | None
    from_default: bool
    content: dict | None


def resave_model(stale):
    """A row's re-save notice (``content_forms.StaleMarkup``), or None.

    ``removed`` is the markup today's sanitizer drops, in plain words;
    ``lost`` the placeholders only that markup held; ``blocked`` whether
    sending refuses the cleaned form, as the page's "Can't be sent until
    fixed".
    """
    if not stale:
        return None
    return {
        "removed": [str(item) for item in stale.removed],
        "lost": list(stale.lost),
        "blocked": stale.blocked,
    }


def catalog_model(campaign_id, version, pages, emails):
    """``catalog_entries``'s rows as ``content list`` prints them.

    The page's ``label`` is printed as ``name``; its links, element IDs and
    the Delete dialog's ``name`` are browser chrome and are left out. The
    confirmation email built from a retired closing note alone (#260) has
    no saved revision, so its ``id`` is None.
    """
    return ContentList(
        campaign_id=campaign_id,
        version=version,
        pages=[
            {
                "slot": page["slot"],
                "name": str(page["label"]),
                "state": page["state"],
                "resave": resave_model(page["resave"]),
            }
            for page in pages
        ],
        emails=[
            {
                "slot": email["slot"],
                "name": str(email["label"]),
                "scheduled": email["scheduled"],
                "singleton": email["singleton"],
                "revisions": [
                    {
                        "id": revision.get("id"),
                        "subject": revision["subject"],
                        "excerpt": revision.get("excerpt", ""),
                        "used_by": [str(name) for name in revision.get("used_by", [])],
                        "deletable": revision["deletable"],
                        "state": revision["state"],
                        "resave": resave_model(revision["resave"]),
                    }
                    for revision in email["revisions"]
                ],
            }
            for email in emails
        ],
    )


def editor_model(campaign, version, kind, slot, read, *, default=None, banner=None):
    """The editor's read (``editor_content``) as ``content show`` prints it.

    ``read`` is ``editor_content``'s ``(label, previous, note, shown)``;
    ``default`` the slot's default as an editor save stores it, printed
    instead of the saved text (``--default``); ``banner`` the page's "show
    the campaign banner" choice, None where it does not apply. A page has
    no subject and no plain-text field in the editor, so both print as
    None, as does ``generate_text``.
    """
    from .accounts.content_forms import stale_markup, text_is_generated
    from .accounts.content_views import _content_state

    label, previous, _, shown = read
    values = default if default is not None else shown
    email = kind == "email"
    return ContentShow(
        campaign_id=campaign.pk,
        version=version,
        kind=kind,
        slot=slot,
        name=str(label),
        revision_id=previous["id"] if previous else None,
        state=_content_state(shown and {"values": shown}),
        resave=resave_model(stale_markup(shown)),
        from_default=default is not None,
        content=None
        if values is None
        else {
            "subject": values.get("subject") if email else None,
            "html": values["html"],
            "text": values["text"] if email else None,
            # As the editor opens it: checked when the saved plain text is
            # exactly what the HTML generates (always, for a default).
            "generate_text": text_is_generated(values) if email else None,
            "show_banner": banner,
        },
    )


def history_model(read):
    """``retained_content``'s read as ``content history`` prints it."""
    campaign, selected, sample = read["campaign"], read["selected"], read["sample"]
    entries = [
        {
            "id": entry["id"],
            "kind": entry["kind"],
            "slot": entry["slot"],
            "name": str(entry["label"]),
            "subject": entry["subject"],
        }
        for entry in read["entries"]
    ]
    revision = None
    if selected is not None:
        entry = next(row for row in entries if row["id"] == selected["id"])
        revision = {key: entry[key] for key in ("id", "kind", "slot", "name")} | {
            # The page shows "Empty" for no sample; a selected revision
            # always renders one.
            "sample": None
            if sample is None
            else {key: sample.get(key) for key in ("subject", "html", "text")}
        }
    return ContentHistory(
        campaign_id=campaign.pk,
        state=campaign.state,
        configuration_id=read["version"].pk,
        entries=entries,
        revision=revision,
    )


def _current(service):
    """The current campaign's id, or ``not_available`` when there is none."""
    from .accounts.admin_editing import editable_configuration

    target = editable_configuration(service).current_campaign_id
    if target is None:
        raise NotAvailable("There is no current campaign.")
    return target


def read_content_list(caller, service):
    """``content list``: the Pages and emails page, Administrators only.

    As on the page, no current campaign is ``not_available``, and background
    work holding the campaign is ``stale_version`` (the page's "can't be
    edited right now"), each reported after the session recheck so an
    ended session is exit 5. A configuration refusal is ``unavailable``.
    """
    from .accounts.campaign_views import _state
    from .accounts.content_views import _campaign, catalog_entries
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    def step():
        """Admit, read in one snapshot, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        try:
            with read_transaction():
                target = _current(service)
                state = _state(service)
                campaign = _campaign(state, target)
                configuration = state[0]
                model = catalog_model(
                    campaign.pk,
                    configuration.active_configuration.digest,
                    *catalog_entries(configuration, campaign),
                )
        except KeyError:
            # A KeyError is a bug, never "no such campaign": internal.
            raise
        except (LookupError, NotAvailable):
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise NotAvailable("No such campaign.") from None
        except StaleRecordError:
            # The page's hold refusal; the session first, as for the others.
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)


def read_content_history(caller, service, revision_id=None):
    """``content history``: the Content history page, Administrators only.

    No current campaign, a purged one, its retained configuration gone, or
    an unknown ``revision_id`` is ``not_available``, reported after the
    session recheck so an ended session is exit 5. A configuration refusal
    is ``unavailable``.
    """
    from .accounts.content_history import retained_content
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction

    def step():
        """Admit, read in one snapshot, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        try:
            with read_transaction():
                model = history_model(
                    retained_content(service, _current(service), revision_id)
                )
        except KeyError:
            # A KeyError is a bug, never "no such revision": internal.
            raise
        except (LookupError, NotAvailable):
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise NotAvailable("The retained content is not available.") from None
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)


def read_content_show(caller, service, kind, slot, revision_id=None, default=False):
    """``content show``: the content editor as it opens, Administrators only.

    As on the page, the current campaign only (none is ``not_available``),
    and background work holding it is ``stale_version``; an unknown slot or
    revision is ``not_available``. Each is reported after the session
    recheck so an ended session is exit 5. A configuration refusal is
    ``unavailable``.
    """
    from .accounts.campaign_views import _state
    from .accounts.content_forms import default_values
    from .accounts.content_views import _campaign, _shows_banner, editor_content
    from .accounts.policy import Capability
    from .campaigns.work_locks import read_transaction
    from .storage import StaleRecordError

    def step():
        """Admit, read in one snapshot, then recheck."""
        actor = _admit(caller, service.store, Capability.CONFIGURE)
        try:
            with read_transaction():
                target = _current(service)
                state = _state(service)
                campaign = _campaign(state, target)
                configuration = state[0]
                read = editor_content(configuration, campaign, kind, slot, revision_id)
                model = editor_model(
                    campaign,
                    configuration.active_configuration.digest,
                    kind,
                    slot,
                    read,
                    # The page's "?start=default": the default as an editor
                    # save stores it (cleaned HTML, generated plain text).
                    default=default_values(kind, slot, campaign_id=campaign.pk)
                    if default
                    else None,
                    banner=_shows_banner(campaign, kind, slot),
                )
        except KeyError:
            # A KeyError is a bug, never "no such slot": internal.
            raise
        except (LookupError, NotAvailable):
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise NotAvailable("That page or email is not available.") from None
        except StaleRecordError:
            # The page's hold refusal; the session first, as for the others.
            _recheck(caller, service.store, actor, Capability.CONFIGURE)
            raise
        _recheck(caller, service.store, actor, Capability.CONFIGURE)
        return model

    return _held(step)


@dataclass(frozen=True)
class ContentPreview(ReadModel):
    """``content preview``: a Delete to confirm, or the plain-text preview.

    ``action`` says which: ``delete`` (the email tables' Delete) or
    ``plain_text`` (the editors' live preview of typed HTML). For
    ``delete``, ``version`` is the applied configuration's version the
    removal was built against, ``campaign_id`` the current campaign,
    ``deleting`` each chosen email (``id``, ``slot``, ``subject``) and
    ``preview.token`` the signed binding ``content confirm`` takes, valid
    for fifteen minutes; ``plain_text`` is None. For ``plain_text``, only
    ``plain_text`` is set: ``html`` (the sanitized HTML), ``text`` (the
    plain text generated from it) and ``removed`` (the markup the sanitizer
    drops, in plain words).
    """

    action: str
    version: str | None
    campaign_id: UUID | None
    deleting: list | None
    plain_text: dict | None
    preview: dict | None


@dataclass(frozen=True)
class ContentConfirm(ReadModel):
    """The configuration request a confirmation recorded, as its status reads.

    ``created`` is false when this token's request already existed (a
    repeated confirm), which then returns the original request unchanged.
    """

    created: bool
    request: dict


def admit_preview(caller, service, *, delete):
    """The role first (#1341): ``content preview``'s admission, before any input.

    The Delete mode admits as the page's Delete post does (``CONFIGURE``,
    recording activity); the plain-text mode passively, as the editors'
    preview post checks a read-only authorization that renews nothing.
    Either way Staff are ``denied`` before an input is read.
    """
    from .accounts.policy import Capability
    from .admin_changes import _admit_change

    if delete:
        return _admit_change(caller, service)
    return _admit(caller, service.store, Capability.CONFIGURE)


def plain_text_preview(source):
    """``content preview --html``: the editors' live preview of ``source``.

    Through the page's own ``content_views.plain_text``, so the two answer
    alike. Nothing is read or recorded. Source the sanitizer refuses (too
    long) is ``invalid``.
    """
    from .accounts.content_views import plain_text

    return ContentPreview(
        action="plain_text",
        version=None,
        campaign_id=None,
        deleting=None,
        plain_text=plain_text(source),
        preview=None,
    )


def _delete_refusal(error):
    """A page refusal (``UserFacingError``) as an ``invalid`` problem at ``delete``."""
    from .admin_changes import InvalidChange

    refusal = error.refusal
    text = " ".join(str(item) for item in (refusal.message, refusal.fix) if item)
    return InvalidChange([{"field": "delete", "code": "invalid", "message": text}])


def preview_deletion(caller, service, actor, identifiers, *, expected_version):
    """``content preview --delete``: the email tables' Delete, built and signed.

    ``actor`` is ``admit_preview``'s. In the page's work transaction it
    reads the page's pinned state and campaign check
    (``content_views._content_campaign``: the current campaign only, not
    archived, not while background work holds it), checks the version
    (another is ``stale_version``) and builds the page's own removal
    (``content_delete_views.review_deletion``). The page's refusals of the
    choice (an email that can't be deleted, one a schedule still sends, a
    repeat, a configuration made invalid) are ``invalid`` at ``delete``;
    held or not current is ``stale_version``; archived or no current
    campaign ``not_available``. Rechecks access afterwards. Changes nothing
    and records no event, as building the page's change records none.
    """
    from .accounts.campaign_views import _state
    from .accounts.content_delete_views import chosen_ids, review_deletion
    from .accounts.content_views import _content_campaign, _records
    from .accounts.policy import Capability
    from .admin_changes import _ended_or_raise
    from .campaigns.work_locks import work_transaction
    from .storage import StaleRecordError
    from .web.refusals import UserFacingError, UserFacingUnavailable

    try:
        chosen = chosen_ids([str(value) for value in identifiers])
    except UserFacingError as error:
        raise _delete_refusal(error) from None

    def step():
        """Check, build and sign in one work transaction, as the page's post does."""
        with work_transaction():
            target = _current(service)
            state = _state(service)
            try:
                campaign = _content_campaign(state, target)
            except LookupError:
                raise NotAvailable("There is no current campaign.") from None
            except UserFacingUnavailable as error:
                # Archived: the menu's own reason, permanent (not stale).
                raise NotAvailable(str(error)) from None
            configuration = state[0]
            if configuration.active_configuration.digest != expected_version:
                raise StaleRecordError("The configuration changed since then.")
            try:
                token = review_deletion(service, actor, state, campaign, chosen)
            except UserFacingError as error:
                raise _delete_refusal(error) from None
            by_id = {row["id"]: row for row in _records(configuration, campaign.pk)}
            return ContentPreview(
                action="delete",
                version=expected_version,
                campaign_id=campaign.pk,
                deleting=[
                    {
                        "id": identifier,
                        "slot": by_id[identifier]["values"]["slot"],
                        "subject": by_id[identifier]["values"].get("subject", ""),
                    }
                    for identifier in chosen
                ],
                plain_text=None,
                preview={"token": token},
            )

    try:
        model = _held(step)
    except (PermissionError, NotAvailable, StaleRecordError, ValueError) as error:
        # Each refusal after the session recheck, so an ended session is
        # exit 5 rather than the refusal.
        _ended_or_raise(caller, service, actor, error)
    _recheck(caller, service.store, actor, Capability.CONFIGURE)
    return model


def confirm_content(caller, service, actor, *, token, context):
    """``content confirm``: the Delete dialog's confirmation of a built removal.

    ``actor`` is the role-first admission (``admin_changes._admit_change``,
    as the page's post). ``confirm_intent`` checks the token with the page's
    delete salt for the current campaign and the page's scope
    (``campaign_views._scope``): expired or out of date is
    ``stale_version``; altered or another campaign's ``invalid``; another
    Administrator's ``denied``. It records the request with
    ``admin_cmd_content_confirm`` in its own durable transaction; a repeat
    returns the original request and records nothing.
    ``context["committed"]`` and ``context["request_id"]`` follow ``schedule
    confirm``'s rules, so a later error is ``outcome_unknown`` naming the
    request.
    """
    from django.core import signing
    from django.db import DatabaseError

    from .accounts.admin_editing import confirm_intent
    from .accounts.campaign_views import _scope
    from .accounts.configuration_requests import policy_operation_id
    from .accounts.content_delete_views import delete_salt
    from .admin_changes import _ended_or_raise
    from .admin_reads import config_request
    from .audit.schemas import Action, ActorKind, Outcome
    from .audit.services import record_action
    from .observability import _guard_refusal

    try:
        salt = delete_salt(_held(lambda: _current(service)))
    except NotAvailable as error:
        _ended_or_raise(caller, service, actor, error)
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
            Action.ADMIN_CMD_CONTENT_CONFIRM,
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
        # Altered, or signed for another campaign (an expired one is stale).
        raise ValueError("The preview token is not valid.") from None
    except PermissionError as error:
        _ended_or_raise(caller, service, actor, error)
    except DatabaseError as error:
        if created and not _guard_refusal(error):
            context["committed"] = True
        raise
    context["committed"] = True
    return ContentConfirm(
        created=bool(created), request=config_request(receipt).to_document()
    )
