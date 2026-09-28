"""Named temporary page/email editing using the normal sanitized visual controls."""

from urllib.parse import urlencode
from uuid import uuid4

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.web.content import PLACEHOLDERS, sanitize_html
from parishkit.stewardship.web.contracts import expected_version, filters
from parishkit.stewardship.web.refusals import UserFacingError, stale_page

from . import setup_help
from .authentication import runtime
from .content_defaults import default_initial
from .content_forms import (
    EMAIL_ADDITIONS,
    EMAIL_LABELS,
    ContentForm,
    applicable_slots,
    matches_default,
    page_slots,
    sample_render,
    text_is_generated,
)
from .setup_content import (
    FILL_ALL,
    FILL_EMPTY,
    content_label,
    default_updates,
    draft_campaign,
    first_campaign_missing,
)
from .setup_drafts import save_section, save_sections, view_draft
from .setup_views import ERRORS, _checked, _closed, _context, page_error


class SetupContentForm(ContentForm):
    """The original attempt version replaces the active YAML digest in setup."""

    base_digest = None

    def __init__(self, *args, **kwargs):
        """Explain each content field; slot-specific rules keep their own help."""
        super().__init__(*args, **kwargs)
        setup_help.apply(self, setup_help.CONTENT)


def _draft(request, service):
    """Do not create a wizard or let another login select a content owner."""
    draft = view_draft(request, service)
    if draft is None:
        raise first_campaign_missing()
    return draft_campaign(request, service, draft.status.attempt_id)


def _saved(draft, kind, slot):
    """A slot counts as saved only while it holds content, not a clear marker."""
    return bool(draft.sections.get(f"{kind}_{slot}", {}).get("values"))


def _state(draft, kind, slot):
    """List status of one slot: "empty", "default" (unmodified) or "custom"."""
    values = draft.sections.get(f"{kind}_{slot}", {}).get("values")
    if not values:
        return "empty"
    return "default" if matches_default(values) else "custom"


def _draft_parish(draft):
    """Sample parish facts, including the draft's outgoing-mail Reply-to address."""
    return draft.sections["parish"] | {
        "email": draft.sections.get("mail", {}).get("reply_to", "")
    }


# The fill-in redirect reports its counts under one of these prefixes.
RESULTS = ("filled", "reset")


def _filled(parameters):
    """Parse the bounded counts the fill-in or reset redirect reports, if present.

    Returns ``None`` or ``{"action": "filled"|"reset", "pages": n, "emails": n}``.
    """
    names = {f"{action}_{kind}" for action in RESULTS for kind in ("pages", "emails")}
    values = filters(parameters, allowed=names)
    if not values:
        return None
    action = next(iter(values)).partition("_")[0]
    if set(values) != {f"{action}_pages", f"{action}_emails"} or any(
        not value.isdigit() or len(value) > 2 for value in values.values()
    ):
        raise ValueError("Invalid fill-in result.")
    return {
        "action": action,
        "pages": int(values[f"{action}_pages"]),
        "emails": int(values[f"{action}_emails"]),
    }


def _fill_defaults(request, service):
    """Save default text into applicable slots in one versioned edit.

    Without ``reset`` this fills every empty slot, including ones the Admin
    cleared (the automatic fill on the campaign save keeps a clear; this
    button is the explicit way to refill it). With ``reset`` and its
    required ``confirm`` box it replaces every applicable slot's text with
    its default; schedules that send a replaced email follow the new
    revision (see ``reconcile_preparation``). The defaults use the same
    validation as a manual save (``default_updates``). All slots are saved
    together under the version the Admin's page was rendered with, so a
    concurrent edit in another tab makes the whole fill-in stale rather than
    racing it slot by slot.
    """
    _closed(request, {"version", "reset", "confirm"})
    reset = request.POST.get("reset") == "on"
    if set(request.POST) & {"reset", "confirm"} and not (
        reset and request.POST.get("confirm") == "on"
    ):
        raise UserFacingError(
            _("Nothing was reset: the confirmation box was not ticked."),
            fix=_(
                "To replace every page and email with its default text, tick "
                "the confirmation box and submit again."
            ),
            link=reverse("admin:setup_content"),
            link_label=_("Back to the content list"),
        )
    draft, campaign = _draft(request, service)
    version = expected_version(request.POST.get("version"))
    if version != draft.status.version:
        raise stale_page()
    updates = default_updates(
        draft.sections,
        campaign,
        draft.status.attempt_id,
        which=FILL_ALL if reset else FILL_EMPTY,
    )
    if updates:
        save_sections(
            request,
            service,
            draft.status.attempt_id,
            updates=updates,
            expected_version=version,
        )
    return _checked(
        request,
        service,
        HttpResponseRedirect(
            result_url("reset" if reset else "filled", updates.keys())
        ),
    )


def result_url(action, steps):
    """The content list URL that reports how many page/email ``steps`` were filled.

    ``_filled`` parses this back; the list then names the slots kept as-is.
    """
    # Page slots only ever sent inside an email count as emails, matching
    # where the content list shows them.
    additions = {f"page_{slot}" for slot in EMAIL_ADDITIONS}
    kinds = ["email" if step in additions else step.split("_", 1)[0] for step in steps]
    query = urlencode(
        {f"{action}_{kind}s": kinds.count(kind) for kind in ("page", "email")}
    )
    return reverse("admin:setup_content") + "?" + query


@require_http_methods(["GET", "HEAD", "POST"])
def setup_content(request):
    """Show enabled named pages and independent email slots, including empty slots.

    A POST fills every empty applicable slot with its default text, or resets
    every slot to it (see ``_fill_defaults``).
    """
    try:
        service = runtime()
        if request.method == "POST":
            return _fill_defaults(request, service)
        filled = _filled(request.GET)
        draft, campaign = _draft(request, service)
        groups = []
        pages = page_slots(campaign)
        # Content only ever sent inside an email is listed right after that
        # email, not with the Family pages; its kind and slot stay "page".
        listed = {
            "page": [
                ("page", slot, label)
                for slot, label in pages.items()
                if slot not in EMAIL_ADDITIONS
            ],
            "email": [
                entry
                for slot, label in EMAIL_LABELS.items()
                for entry in [("email", slot, label)]
                + [
                    ("page", page, pages[page])
                    for page, email in EMAIL_ADDITIONS.items()
                    if email == slot and page in pages
                ]
            ],
        }
        for group, slots in listed.items():
            groups.append(
                {
                    "kind": group,
                    "entries": [
                        {
                            "label": label,
                            "url": reverse(
                                "admin:setup_content_edit", args=[kind, slot]
                            ),
                            "state": _state(draft, kind, slot),
                        }
                        for kind, slot, label in slots
                    ],
                }
            )
        # After a fill, name every slot it left alone because it holds the
        # Admin's own text, so "nothing happened" there is never a surprise.
        kept = (
            [
                entry
                for group in groups
                for entry in group["entries"]
                if entry["state"] == "custom"
            ]
            if filled and filled["action"] == "filled"
            else []
        )
        response = render(
            request,
            "stewardship/setup-content.html",
            _context(draft, "content")
            | {
                "groups": groups,
                "campaign_name": campaign["name"],
                "filled": filled,
                "kept": kept,
                "fillable": any(
                    not _saved(draft, kind, slot)
                    for kind, slot in applicable_slots(campaign)
                ),
            },
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "content")


@require_http_methods(["GET", "HEAD", "POST"])
def setup_content_edit(request, kind, slot):
    """Every save stays temporary; the sample never reads real Family information."""
    try:
        service = runtime()
        draft, campaign = _draft(request, service)
        label = content_label(campaign, kind, slot)
        step = f"{kind}_{slot}"
        previous = draft.sections.get(step, {}).get("values")
        # "?start=default" pre-fills the editor with the slot's default text
        # without saving anything: it starts an empty slot, or resets a saved
        # one. Saving still needs the normal POST, and leaving the page
        # discards the unsaved default, so nothing is replaced without a save.
        start = request.method != "POST" and request.GET.get("start") == "default"
        initial = {
            name: value
            for name, value in (previous or {}).items()
            if name in SetupContentForm.base_fields
        }
        initial["generate_text"] = text_is_generated(previous)
        if start:
            initial = default_initial(kind, slot)
        form = SetupContentForm(
            request.POST if request.method == "POST" else None,
            kind=kind,
            slot=slot,
            initial=initial,
        )
        # Accept every editor field, not just this slot's: a tab opened before
        # web-only pages lost their plain-text controls (#259) still posts
        # them, and the form ignores them rather than losing the Admin's HTML.
        _closed(
            request,
            {*ContentForm.base_fields, "version"}
            - ({"subject", "base_digest"} if kind == "page" else {"base_digest"}),
            query={"start"} if request.method != "POST" else frozenset(),
        )
        if request.GET and not start:
            raise ValueError("Invalid content parameters.")
        status, refusal = 200, None
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise stale_page()
            if form.is_valid():
                values = form.values(campaign_id=draft.status.attempt_id, slot=slot)
                record = (
                    {"id": str(uuid4()), "values": values}
                    if values
                    else {"id": None, "values": None}
                )
                try:
                    save_section(
                        request,
                        service,
                        draft.status.attempt_id,
                        step=step,
                        values=record,
                        expected_version=version,
                    )
                    return _checked(
                        request,
                        service,
                        HttpResponseRedirect(reverse("admin:setup_content")),
                    )
                except UserFacingError as error:
                    # Show a correctable refusal (such as clearing a
                    # template a schedule still sends) beside the form,
                    # which keeps what the Admin entered.
                    form.add_error(None, error.refusal.message)
                    refusal = error.refusal
            status = 400
        try:
            visual = sanitize_html(form["html"].value() or "")
        except ValueError:
            visual = ""
        sample = sample_render(previous, parish=_draft_parish(draft), campaign=campaign)
        response = render(
            request,
            "stewardship/setup-content-edit.html",
            _context(draft, "content")
            | {
                "form": form,
                "label": label,
                "visual": visual,
                "placeholders": sorted(PLACEHOLDERS),
                "sample": sample,
                "default_url": (
                    None
                    if start
                    else reverse("admin:setup_content_edit", args=[kind, slot])
                    + "?start=default"
                ),
                # A saved slot offers "Reset"; an empty one offers "Start".
                "saved": previous is not None,
                "started_from_default": start,
                "refusal": refusal,
                # Post to the clean path: the "?start=default" GET must not
                # carry its query into the POST, which accepts none.
                "post_url": request.path,
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "content")
