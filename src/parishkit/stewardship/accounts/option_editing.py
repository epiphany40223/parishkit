"""The ordered-option editor (Share options), reviewed in the review dialog.

It edits one ordered list of labelled options on the current draft campaign
with the share formset (``share_forms.ShareOptions``): the whole list on an
unlocked Testing draft, and only the short labels once the structure is
fixed (#1569, ``short_labels_only``), with the module the list belongs to.
``OptionEditor`` names the values key, the module, the page and its words;
the Member talents editor that once shared it was removed with talents
(#970).

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

Inside the campaign wizard (#1404 PR 4) the form's Save and continue posts
``save`` with the page's request key instead: the same Review, recorded at
once by ``campaign_wizard.save_step`` and answered with the change's
status, which the page follows before it opens the next step.
"""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.utils.translation import gettext_lazy as _

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.configuration import without_short_labels
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    stale_page,
    with_default_code,
)

from . import admin_context, admin_navigation, campaign_wizard
from .admin_editing import (
    confirm,
    direct_key,
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
from .share_forms import SHORT_LABELS_ONLY, ShareOptions, share_action


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


def live_short_only():
    """The refusal of a locked campaign's Share options change beyond the
    short labels (#1569): a form drawn before the campaign was locked, or a
    request built by hand. Shown in the review dialog, as a stale page is."""
    return UserFacingStale(
        SHORT_LABELS_ONLY,
        fix=_("Reload the page to edit the short labels."),
        reason="stale_page",
    )


def short_labels_only(configuration, campaign, held, editable):
    """Whether Share options may change only its short labels now (#1569).

    True for the current, unarchived campaign whose settings cannot be
    edited (it is live or has been, or it is outside Testing mode) while no
    background work holds the settings: the
    short label is Admin-only wording, outside the Family form and its
    digest, so the structure lock (``admission._locked_part`` and SQL's
    stewardship_campaign_pointer_v1, migration 0166) leaves it open.
    """
    return (
        not editable
        and not held
        and campaign.pk == configuration.current_campaign_id
        and campaign.state != "archived"
    )


def structure_posted(post, count):
    """Whether a post names a field a live campaign's form leaves disabled.

    The browser never sends a disabled field, so such a value comes from a
    form drawn before the campaign went live, or from a request built by
    hand; either is refused rather than silently ignored.
    """
    return any(
        f"options-{index}-{name}" in post
        for index in range(count)
        for name in ("label", "free_text", "ORDER", "DELETE")
    )


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
            "short_only": formset.short_only,
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
        # The refusal log's code for this page's own refusal (#1444).
        with_default_code(response)
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
    added or removed, a relabelled option, a changed short label (#1569), a
    changed free-text response and an option the reader moved, with its old
    and new position. Options that only shift because another was added,
    removed or moved have no line. Lines follow the new order, then the
    removed options in their old one.
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

    def short(option):
        """An option's short label (#1569), or "Not set" when it has none."""
        return option.get("short_label") or str(_("Not set"))

    lines = []
    for position, option in enumerate(after, start=1):
        named = {"label": option["label"]}
        if option["id"] not in old:
            lines.append({"label": _("Added"), "before": None, "after": shown(option)})
            # An added option's short label is its own line, so the Added
            # line reads as before when it has none.
            if option.get("short_label"):
                lines.append(
                    {
                        "label": _("Short label for “%(label)s”") % named,
                        "before": short({}),
                        "after": short(option),
                    }
                )
            continue
        index, previous = old[option["id"]]
        if previous["label"] != option["label"]:
            lines.append(
                {
                    "label": _("Label"),
                    "before": previous["label"],
                    "after": option["label"],
                }
            )
        if previous.get("short_label", "") != option.get("short_label", ""):
            lines.append(
                {
                    "label": _("Short label for “%(label)s”") % named,
                    "before": short(previous),
                    "after": short(option),
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


def _preview(request, editor, service, actor, state, campaign, formset, salt, key=None):
    """The signed review, the page refused (400, a response), or a refusal raised.

    ``key`` is a direct save's page-drawn request key (#1404 PR 4); a
    Review shown in the dialog takes a fresh one.
    """
    configuration = state[0]
    if request.POST.get("base_digest") != configuration.active_configuration.digest:
        raise stale_page()
    # The version check must stay first: a refused page is drawn at the
    # current version (``_page``), which is then known to be the posted one,
    # so the rows sent are never paired with a newer version.
    if not formset.is_valid():
        return _page(request, editor, configuration, campaign, formset, status=400)
    return review_options(
        editor, service, actor, state, campaign, formset, salt, key=key
    )


def review_options(editor, service, actor, state, campaign, formset, salt, key=None):
    """The page's review of a valid formset, without the request (#463).

    Shared by the page's Review and ``pk-admin campaign shares-preview``, so
    the two cannot drift. ``state`` is ``campaign_views._state``'s, read in
    the caller's work transaction, and ``formset`` is the page's own
    ``ShareOptions``, already valid. Refuses no change (``UserFacingError``,
    the unchanged safeguard) and a base that moved (``stale_page``); a
    candidate the configuration refuses raises from ``build_candidate``.
    Returns ``changes`` (the compact review's lines), ``options`` (the list
    the change saves) and ``preview``, the signed binding Apply posts.
    ``key`` is a direct save's request key (#1404 PR 4, ``direct_key``).
    """
    configuration, fingerprint = state[0], state[-1]
    options = formset.values()
    before = editor.current(campaign)
    if formset.short_only and without_short_labels(options) != without_short_labels(
        before
    ):
        # The disabled fields keep their saved values, so only a bug gets
        # here; the structure lock would refuse the change anyway.
        raise live_short_only()
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
    return {
        "changes": option_changes(before, options),
        "options": options,
        "preview": sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
            key=key,
        ),
    }


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
    """Current Admins review and request changes: a whole list on an unlocked
    draft, a live campaign's short labels only (#1569).

    GET draws the editor (it takes no query: its address is all there is);
    a Review POST answers the review dialog's fragment; Apply answers with
    the change's status, and so does a direct save inside the campaign
    wizard (``save``, see the module docstring). Refusals are raised for the
    dialog, or the page's status line (see the module docstring).
    """
    try:
        service = runtime()
        actor = principal(request, service)
        filters(request.GET, allowed=set())
        salt = f"{editor.salt}:{campaign_id}"
        action = None
        saved = None
        if request.method == "POST":
            action = share_action(request.POST)
            if action == "confirm":
                response = confirm(
                    request, service, actor, salt=salt, current_scope=_scope
                )
                response["Cache-Control"] = "no-store"
                return response
        key = direct_key(request.POST) if action == "save" else None
        if key is not None and (
            repeat := campaign_wizard.repeated_save(request, actor, key)
        ):
            repeat["Cache-Control"] = "no-store"
            return repeat
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            configuration, campaigns, held = state[0], state[1], state[3]
            campaign, editable = _target(configuration, campaigns, held, campaign_id)
            short_only = short_labels_only(configuration, campaign, held, editable)
            if (
                not (editable or short_only)
                or editor.module not in campaign.active_configuration.values["modules"]
            ):
                raise _unavailable(editor, configuration, campaign)
            previous = editor.current(campaign)
            if (
                action == "preview"
                and short_only
                and structure_posted(request.POST, len(previous) + 1)
            ):
                raise live_short_only()
            formset = ShareOptions(
                request.POST if action in {"preview", "save"} else None,
                prefix="options",
                previous=previous,
                short_only=short_only,
            )
            if action is None:
                response = _page(request, editor, configuration, campaign, formset)
            else:
                review = _preview(
                    request, editor, service, actor, state, campaign, formset, salt, key
                )
                if not isinstance(review, dict):
                    response = review
                elif action == "save":
                    saved = review["preview"]
                else:
                    response = render(
                        request, editor.review_template, {"review": review}
                    )
        if saved is not None:
            response = campaign_wizard.save_step(
                request,
                service,
                actor,
                token=saved,
                salt=salt,
                current_scope=_scope,
                campaign_id=campaign_id,
            )
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
