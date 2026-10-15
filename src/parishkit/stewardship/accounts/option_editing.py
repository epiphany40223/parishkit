"""The ordered-option editor (Share options), reviewed in the review dialog.

It edits one ordered list of labelled options on the current draft campaign
with the share formset (``share_forms.ShareOptions``): an unlocked Testing
draft only, with the module the list belongs to. ``OptionEditor`` names the
values key, the module, the page and its words; the Member talents editor
that once shared it was removed with talents (#970).

Review posts the form without a page load and is answered with the shared
review dialog's fragment (UX conventions, "Review dialog", #750): a compact
review (#1356) marking only what changed, one line each (``option_changes``),
and the signed preview, which the dialog's Apply posts back to the page.
Apply is answered with the change's status, which the dialog follows; once
the change is applied it redraws the form's region from the page's own
address (the applied list and a fresh blank row).
A Review refused for a row's value (or the formset's own) is the page again
with status 400, whose field errors and summary the page script marks on the
form it shows; the dialog stays shut. Any other refusal (a stale page, an
out-of-date preview, a draft that can no longer change, the unchanged
safeguard) is a plain refusal shown in the dialog, while the form keeps the
reader's rows at the version they came from, so the next Review is refused
until the page is reloaded and never quietly undoes a change made elsewhere.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingError, stale_page

from . import admin_context, admin_navigation
from .admin_editing import (
    confirm,
    error_response,
    principal,
    sign_preview,
    summary_errors,
)
from .authentication import runtime
from .campaign_views import _scope, _state, _target
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin
from .share_forms import ShareOptions, share_action


@dataclass(frozen=True)
class OptionEditor:
    """What names one ordered-option editor: its key, module, page and words."""

    key: str  # the campaign values key: "share_options"
    module: str  # the module the list belongs to
    page: str  # the page's URL name
    template: str  # the page's template
    review_template: str  # the review dialog's fragment
    salt: str  # the signed preview's salt, before ":<campaign id>"
    unchanged: str  # the refusal when nothing changed
    unavailable: str  # the refusal when the list cannot be edited now
    current: Callable  # campaign -> the list it shows today


def _page(request, editor, configuration, campaign, formset, *, status=200):
    """Render the editor; a Review refused for its values (400) has its errors.

    The form is drawn at the applied version: a refused Review reaches here
    only after its posted version was found to be that one.
    """
    admin_navigation.place(request, flow="change", step="edit")
    response = render(
        request,
        editor.template,
        {
            "configuration": configuration,
            "campaign": campaign,
            "formset": formset,
            "base_digest": configuration.active_configuration.digest,
            # Review and the review dialog's Apply post here.
            "page_url": request.path,
            # Once a change is applied, the dialog redraws the form's region
            # from this page's own address.
            "refresh_url": request.path,
            "errors": summary_errors(formset) if status == 400 else [],
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


def _stable(sequence):
    """The positions in ``sequence`` of a longest increasing run within it.

    ``sequence`` is each kept option's old index, in its new order. The
    options of a longest increasing subsequence kept their order relative to
    one another; every other kept option is one the reader moved. When runs
    tie (two neighbours swapped), the first found is kept, so the review
    names one of the two as moved, which is true either way. Lists hold at
    most a hundred options, so the quadratic search is plenty.
    """
    best = [[index] for index in range(len(sequence))]
    for index, value in enumerate(sequence):
        for earlier in range(index):
            if sequence[earlier] < value and len(best[earlier]) + 1 > len(best[index]):
                best[index] = [*best[earlier], index]
    return set(max(best, key=len, default=[]))


def option_changes(before, after):
    """The compact review's lines for one ordered-list change (#1356).

    Each line is {label, before, after} for components/compact-review.html.
    The review marks only what changed, never both whole lists: an option
    added or removed, a relabelled option, a changed free-text response and
    an option the reader moved, with its old and new position. Options that
    only shift because another was added, removed or moved have no line.
    Lines follow the new order, then the removed options in their old one.
    """
    old = {option["id"]: (index, option) for index, option in enumerate(before)}
    kept = [option for option in after if option["id"] in old]
    moved_from = _stable([old[option["id"]][0] for option in kept])
    moved = {
        option["id"] for index, option in enumerate(kept) if index not in moved_from
    }

    def shown(option):
        """An option's label, with its free-text response when it has one."""
        if option["free_text"]:
            return _("%(label)s (with a free-text response)") % option
        return option["label"]

    def answer(free_text):
        """Whether an option has a free-text response, in words."""
        return str(_("Yes") if free_text else _("No"))

    lines = []
    for position, option in enumerate(after, start=1):
        if option["id"] not in old:
            lines.append({"label": _("Added"), "before": None, "after": shown(option)})
            continue
        index, previous = old[option["id"]]
        named = {"label": option["label"]}
        if previous["label"] != option["label"]:
            lines.append(
                {
                    "label": _("Label"),
                    "before": previous["label"],
                    "after": option["label"],
                }
            )
        if previous["free_text"] != option["free_text"]:
            lines.append(
                {
                    "label": _("Free-text response for “%(label)s”") % named,
                    "before": answer(previous["free_text"]),
                    "after": answer(option["free_text"]),
                }
            )
        if option["id"] in moved:
            lines.append(
                {
                    "label": _("Position of “%(label)s”") % named,
                    "before": str(index + 1),
                    "after": str(position),
                }
            )
    remaining = {option["id"] for option in after}
    lines.extend(
        {"label": _("Removed"), "before": None, "after": shown(option)}
        for option in before
        if option["id"] not in remaining
    )
    return lines


def _preview(request, editor, service, actor, state, campaign, formset, salt):
    """The review dialog's fragment, the page refused (400), or a refusal raised."""
    configuration, fingerprint = state[0], state[-1]
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise stale_page()
    # The version check must stay first: a refused page is drawn at the
    # current version (``_page``), which is then known to be the posted one,
    # so the rows sent are never paired with a newer version.
    if not formset.is_valid():
        return _page(request, editor, configuration, campaign, formset, status=400)
    options = formset.values()
    before = editor.current(campaign)
    if options == before:
        # Review is unavailable without a change (data-require-change), so
        # only a request that bypasses the page script meets this safeguard.
        raise UserFacingError(editor.unchanged, fix=_("Nothing was changed."))
    patch = [
        {
            "operation": "update",
            "section": "campaigns",
            "id": str(campaign.pk),
            "values": {editor.key: options},
        }
    ]
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise stale_page()
    build_candidate(base, patch, candidate_id=uuid4())
    review = {
        "changes": option_changes(before, options),
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
        ),
    }
    return render(request, editor.review_template, {"review": review})


def _unavailable(editor, configuration, campaign):
    """Why the list cannot be edited now: the menu entry's own reason (#1128).

    A form opened before the campaign went live (or before the mode
    changed) and sent afterwards is refused with the words the menu's
    greyed entry and the page's direct visit show. A refusal the menu does
    not grey out for (another change holding the settings queue) keeps the
    editor's own message.
    """
    state = admin_context.menu_state(campaign, configuration)
    greyed = admin_navigation.MENU_REASONS[editor.page](state)
    if greyed is None:
        return StaleRecordError(editor.unavailable)
    label = admin_navigation.PAGES[editor.page].label
    return admin_navigation.Greyed(label, greyed, None).refusal()


def option_settings(request, campaign_id, editor):
    """Current Admins may review and request changes only for an unlocked draft.

    GET draws the editor (it takes no query: its address is all there is);
    a Review POST answers the review dialog's fragment; Apply answers with
    the change's status. Refusals are raised for the dialog (see the module
    docstring).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        salt = f"{editor.salt}:{campaign_id}"
        action = None
        if request.method == "POST":
            action = share_action(request.POST)
            if action == "confirm":
                response = confirm(
                    request, service, actor, salt=salt, current_scope=_scope
                )
                response["Cache-Control"] = "no-store"
                return response
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            if (
                not editable
                or editor.module not in campaign.active_configuration.values["modules"]
            ):
                raise _unavailable(editor, configuration, campaign)
            formset = ShareOptions(
                request.POST if action == "preview" else None,
                prefix="options",
                previous=editor.current(campaign),
            )
            if action == "preview":
                response = _preview(
                    request, editor, service, actor, state, campaign, formset, salt
                )
            else:
                response = _page(request, editor, configuration, campaign, formset)
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)
