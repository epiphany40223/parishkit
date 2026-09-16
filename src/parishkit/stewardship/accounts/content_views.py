"""Admin-only content catalog, sanitized previews and exact revision requests."""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import PLACEHOLDERS, sanitize_html
from parishkit.stewardship.web.contracts import filters

from .admin_editing import confirm, error_response, form_action, principal, sign_preview
from .authentication import runtime
from .campaign_views import _scope, _state
from .content_forms import (
    EMAIL_LABELS,
    ContentForm,
    page_slots,
    revision_patch,
    sample_render,
)
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .sessions import authenticated_admin


def _campaign(state, campaign_id):
    """Allow content edits after structural lock, only for the current campaign."""
    configuration, campaigns, held = state[0], state[1], state[3]
    campaign = next((row for row in campaigns if row.pk == campaign_id), None)
    if campaign is None:
        raise LookupError("Campaign is unavailable.")
    if held or configuration.current_campaign_id != campaign.pk:
        raise StaleRecordError("Content is not currently editable.")
    return campaign


def _records(configuration, campaign_id):
    """Select only this campaign's revisions from the exact applied YAML document."""
    return [
        row
        for row in configuration.active_configuration.canonical_document[
            "sections"
        ].get("content", [])
        if row["values"]["campaign_id"] == str(campaign_id)
    ]


def _catalog(request, configuration, campaign):
    """List named page slots and independent email revisions for per-mail selection."""
    records = _records(configuration, campaign.pk)
    pages = []
    for slot, label in page_slots(campaign.active_configuration.values).items():
        pages.append(
            {
                "label": label,
                "url": reverse("admin:content_edit", args=[campaign.pk, "page", slot]),
                "configured": any(
                    row["values"]["kind"] == "page" and row["values"]["slot"] == slot
                    for row in records
                ),
            }
        )
    emails = []
    for slot, label in EMAIL_LABELS.items():
        emails.append(
            {
                "label": label,
                "url": reverse("admin:content_edit", args=[campaign.pk, "email", slot]),
                "revisions": [
                    {
                        "subject": row["values"]["subject"],
                        "test_url": reverse(
                            "admin:campaign_mail", args=[campaign.pk, row["id"]]
                        ),
                        "url": reverse(
                            "admin:content_revision",
                            args=[campaign.pk, "email", slot, row["id"]],
                        ),
                    }
                    for row in records
                    if (row["values"]["kind"], row["values"]["slot"]) == ("email", slot)
                ],
            }
        )
    return render(
        request,
        "stewardship/content-catalog.html",
        {"campaign": campaign, "pages": pages, "emails": emails},
    )


def _page(request, form, campaign, label, *, status=200):
    """Never insert rejected user HTML into the visual editor without sanitizing it."""
    try:
        visual = sanitize_html(form["html"].value() or "")
    except ValueError:
        visual = ""
    response = render(
        request,
        "stewardship/content-settings.html",
        {
            "form": form,
            "campaign": campaign,
            "label": label,
            "visual": visual,
            "placeholders": sorted(PLACEHOLDERS),
        },
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(
    request, service, actor, state, campaign, form, label, previous, slot, salt
):
    """Sign sanitized canonical bytes and disclose every affected mail schedule."""
    configuration, fingerprint = state[0], state[-1]
    if not form.is_valid():
        return _page(request, form, campaign, label, status=400)
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload content before editing it.")
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    values = form.values(campaign_id=campaign.pk, slot=slot)
    try:
        patch, affected = revision_patch(base.document(), campaign, previous, values)
        if not patch:
            form.add_error(None, "No content has changed.")
            return _page(request, form, campaign, label, status=400)
        build_candidate(base, patch, candidate_id=uuid4())
        parish = base.document()["sections"]["parish"][0]["values"]
        before = sample_render(
            previous["values"] if previous else None,
            parish=parish,
            campaign=campaign.active_configuration.values,
        )
        after = sample_render(
            values, parish=parish, campaign=campaign.active_configuration.values
        )
        token = sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
        )
        if len(token) > 256_000:
            raise ValueError("Content preview is too large.")
    except (ConfigError, ValueError):
        form.add_error(
            None,
            "Check the content and placeholders. Referenced email templates cannot "
            "be removed. Large content or many affected schedules may need "
            "smaller edits.",
        )
        return _page(request, form, campaign, label, status=400)
    return render(
        request,
        "stewardship/content-preview.html",
        {
            "campaign": campaign,
            "label": label,
            "before": before,
            "after": after,
            "affected": affected,
            "preview": token,
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def content_settings(request, campaign_id, kind=None, slot=None, revision_id=None):
    """Browse and edit content without installing YAML or accessing a real Family."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = (
            f"stewardship-content-preview-v1:{campaign_id}:{kind}:{slot}:{revision_id}"
        )
        if request.GET or request.FILES:
            raise ValueError("Invalid content parameters.")
        if request.method == "POST":
            fields = set(ContentForm.base_fields) - (
                {"subject"} if kind == "page" else set()
            )
            action = form_action(request.POST, preview_fields=fields)
            if kind is None:
                raise ValueError("The catalog is read-only.")
            if action == "confirm":
                return confirm(request, service, actor, salt=salt, current_scope=_scope)
        else:
            filters(request.GET, allowed=set())
        with work_transaction():
            state = _state(service)
            configuration, campaign = state[0], _campaign(state, campaign_id)
            if kind is None:
                response = _catalog(request, configuration, campaign)
            else:
                labels = (
                    page_slots(campaign.active_configuration.values)
                    if kind == "page"
                    else EMAIL_LABELS
                )
                if (
                    kind not in {"page", "email"}
                    or slot not in labels
                    or (revision_id and kind != "email")
                ):
                    raise LookupError("Content slot is unavailable.")
                records = _records(configuration, campaign.pk)
                previous = next(
                    (
                        row
                        for row in records
                        if (row["values"]["kind"], row["values"]["slot"])
                        == (kind, slot)
                        and (kind == "page" or row["id"] == str(revision_id))
                    ),
                    None,
                )
                if revision_id and previous is None:
                    raise LookupError("Content revision is unavailable.")
                initial = (previous["values"] if previous else {}) | {
                    "base_digest": configuration.active_configuration.digest,
                    "generate_text": previous is None,
                }
                form = ContentForm(
                    request.POST if request.method == "POST" else None,
                    kind=kind,
                    slot=slot,
                    initial=initial,
                )
                response = (
                    _preview(
                        request,
                        service,
                        actor,
                        state,
                        campaign,
                        form,
                        labels[slot],
                        previous,
                        slot,
                        salt,
                    )
                    if request.method == "POST"
                    else _page(request, form, campaign, labels[slot])
                )
            if not allows(
                authenticated_admin(request, store=service.store, read_only=True),
                Capability.CONFIGURE,
            ):
                raise PermissionError("Content access was revoked.")
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
