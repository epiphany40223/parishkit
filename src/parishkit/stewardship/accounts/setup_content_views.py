"""Named temporary page/email editing using the normal sanitized visual controls."""

from urllib.parse import urlencode
from uuid import uuid4

from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import PLACEHOLDERS, sanitize_html
from parishkit.stewardship.web.contracts import expected_version, filters

from . import setup_help
from .authentication import runtime
from .content_defaults import default_data, default_initial
from .content_forms import EMAIL_LABELS, ContentForm, page_slots, sample_render
from .setup_content import content_label, draft_campaign
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
        raise LookupError(
            "Start and prepare the first campaign before editing content."
        )
    return draft_campaign(request, service, draft.status.attempt_id)


def _slots(campaign):
    """Every applicable (kind, slot, label), in the order the content list shows."""
    return [
        (kind, slot, label)
        for kind, labels in (("page", page_slots(campaign)), ("email", EMAIL_LABELS))
        for slot, label in labels.items()
    ]


def _saved(draft, kind, slot):
    """A slot counts as saved only while it holds content, not a clear marker."""
    return bool(draft.sections.get(f"{kind}_{slot}", {}).get("values"))


def _draft_parish(draft):
    """Sample parish facts, including the draft's outgoing-mail Reply-to address."""
    return draft.sections["parish"] | {
        "email": draft.sections.get("mail", {}).get("reply_to", "")
    }


def _filled(parameters):
    """Parse the bounded counts the fill-in redirect reports, if present."""
    values = filters(parameters, allowed={"filled_pages", "filled_emails"})
    if not values:
        return None
    if set(values) != {"filled_pages", "filled_emails"} or any(
        not value.isdigit() or len(value) > 2 for value in values.values()
    ):
        raise ValueError("Invalid fill-in result.")
    return {name: int(value) for name, value in values.items()}


def _fill_defaults(request, service):
    """Save the default text into every applicable empty slot in one versioned edit.

    Each default passes through the same SetupContentForm and values() path a
    manual save uses, so it is sanitized and validated identically. Slots that
    already hold saved content are never touched. All new slots are saved
    together under the version the Admin's page was rendered with, so a
    concurrent edit in another tab makes the whole fill-in stale rather than
    racing it slot by slot.
    """
    _closed(request, {"version"})
    draft, campaign = _draft(request, service)
    version = expected_version(request.POST.get("version"))
    if version != draft.status.version:
        raise StaleRecordError("Reload the first-campaign content.")
    updates, counts = {}, {"page": 0, "email": 0}
    for kind, slot, _ in _slots(campaign):
        if _saved(draft, kind, slot):
            continue
        form = SetupContentForm(default_data(kind, slot), kind=kind, slot=slot)
        if not form.is_valid():
            # Unit tests validate every default; this is defensive only.
            raise ValueError("Default content failed validation.")
        values = form.values(campaign_id=draft.status.attempt_id, slot=slot)
        updates[f"{kind}_{slot}"] = {"id": str(uuid4()), "values": values}
        counts[kind] += 1
    if updates:
        save_sections(
            request,
            service,
            draft.status.attempt_id,
            updates=updates,
            expected_version=version,
        )
    query = urlencode(
        {"filled_pages": counts["page"], "filled_emails": counts["email"]}
    )
    return _checked(
        request,
        service,
        HttpResponseRedirect(reverse("admin:setup_content") + "?" + query),
    )


@require_http_methods(["GET", "HEAD", "POST"])
def setup_content(request):
    """Show enabled named pages and independent email slots, including empty slots.

    A POST fills every empty applicable slot with its default text.
    """
    try:
        service = runtime()
        if request.method == "POST":
            return _fill_defaults(request, service)
        filled = _filled(request.GET)
        draft, campaign = _draft(request, service)
        groups = []
        for kind, labels in (("page", page_slots(campaign)), ("email", EMAIL_LABELS)):
            groups.append(
                {
                    "kind": kind,
                    "entries": [
                        {
                            "label": label,
                            "url": reverse(
                                "admin:setup_content_edit", args=[kind, slot]
                            ),
                            "saved": _saved(draft, kind, slot),
                        }
                        for slot, label in labels.items()
                    ],
                }
            )
        response = render(
            request,
            "stewardship/setup-content.html",
            _context(draft, "content")
            | {
                "groups": groups,
                "campaign_name": campaign["name"],
                "filled": filled,
                "fillable": any(
                    not _saved(draft, kind, slot) for kind, slot, _ in _slots(campaign)
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
        # "?start=default" pre-fills an empty slot's editor with its default
        # text without saving anything; saving still needs the normal POST.
        start = request.method != "POST" and request.GET.get("start") == "default"
        initial = {
            name: value
            for name, value in (previous or {}).items()
            if name in SetupContentForm.base_fields
        }
        initial["generate_text"] = previous is None
        if start and previous is None:
            initial = default_initial(kind, slot)
        form = SetupContentForm(
            request.POST if request.method == "POST" else None,
            kind=kind,
            slot=slot,
            initial=initial,
        )
        _closed(
            request,
            {*form.fields, "version"},
            query={"start"} if request.method != "POST" else frozenset(),
        )
        if request.GET and not start:
            raise ValueError("Invalid content parameters.")
        status = 200
        if request.method == "POST":
            version = expected_version(request.POST.get("version"))
            if version != draft.status.version:
                raise StaleRecordError("Reload the first-campaign content.")
            if form.is_valid():
                values = form.values(campaign_id=draft.status.attempt_id, slot=slot)
                record = (
                    {"id": str(uuid4()), "values": values}
                    if values
                    else {"id": None, "values": None}
                )
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
                    reverse("admin:setup_content_edit", args=[kind, slot])
                    + "?start=default"
                    if previous is None and not start
                    else None
                ),
                "started_from_default": start and previous is None,
            },
            status=status,
        )
        return _checked(request, service, response, draft)
    except ERRORS as error:
        return page_error(request, error, "content")
