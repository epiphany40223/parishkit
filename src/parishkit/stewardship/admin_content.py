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
