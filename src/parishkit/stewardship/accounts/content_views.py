"""Admin-only content catalog, sanitized previews and exact revision requests."""

from uuid import uuid4

from django.core import signing
from django.db import DatabaseError
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods, require_POST

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.jobs.campaign_mail_values import document_parish
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.content import (
    PLACEHOLDERS,
    prepare_content,
    removed_markup,
    sanitize_html,
)
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import (
    UserFacingError,
    UserFacingStale,
    stale_page,
)

from .admin_editing import confirm, error_response, form_action, principal, sign_preview
from .authentication import runtime
from .campaign_views import _scope, _state
from .content_defaults import default_initial
from .content_forms import (
    EMAIL_LABELS,
    ContentForm,
    matches_default,
    page_slots,
    revision_patch,
    sample_render,
    text_is_generated,
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
        raise UserFacingStale(
            _("This campaign's pages and emails can't be edited right now."),
            fix=_(
                "Only the current campaign's content can be edited, and not "
                "while mail delivery or other background work is running. "
                "Try again later."
            ),
        )
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


def _content_state(record):
    """Catalog status of one revision: "empty", "default" (unmodified) or "custom"."""
    if record is None:
        return "empty"
    return "default" if matches_default(record["values"]) else "custom"


def _catalog(request, configuration, campaign):
    """List named page slots and independent email revisions for per-mail selection."""
    records = _records(configuration, campaign.pk)
    pages = []
    for slot, label in page_slots(campaign.active_configuration.values).items():
        record = next(
            (
                row
                for row in records
                if (row["values"]["kind"], row["values"]["slot"]) == ("page", slot)
            ),
            None,
        )
        pages.append(
            {
                "label": label,
                "url": reverse("admin:content_edit", args=[campaign.pk, "page", slot]),
                "state": _content_state(record),
            }
        )
    emails = []
    for slot, label in EMAIL_LABELS.items():
        emails.append(
            {
                "label": label,
                "singleton": slot == "confirmation",
                "url": reverse("admin:content_edit", args=[campaign.pk, "email", slot]),
                "revisions": [
                    {
                        "subject": row["values"]["subject"],
                        "state": _content_state(row),
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


def _page(
    request,
    form,
    campaign,
    label,
    *,
    status=200,
    default_url=None,
    saved=False,
    started=False,
    refusal=None,
):
    """Never insert rejected user HTML into the visual editor without sanitizing it.

    ``default_url`` offers to start an empty slot (or, when ``saved``, reset a
    configured one) from its default text; ``started`` says the form now
    holds that unsaved default.
    """
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
            "default_url": default_url,
            "saved": saved,
            "started_from_default": started,
            "refusal": refusal,
            # Post to the clean path: a "?start=default" GET must not carry its
            # query into the POST, which accepts no query parameters.
            "post_url": request.path,
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
        raise stale_page()
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    values = form.values(campaign_id=campaign.pk, slot=slot)
    try:
        try:
            patch, affected = revision_patch(
                base.document(), campaign, previous, values
            )
        except UserFacingError as error:
            # A correctable refusal (removing a template that a schedule
            # still sends) is shown beside the form, which keeps its input.
            form.add_error(None, error.refusal.message)
            return _page(
                request, form, campaign, label, status=400, refusal=error.refusal
            )
        if not patch:
            form.add_error(None, "No content has changed.")
            return _page(request, form, campaign, label, status=400)
        build_candidate(base, patch, candidate_id=uuid4())
        parish = document_parish(base.document())
        from parishkit.stewardship.jobs.receipt_preview import confirmation_block

        receipt = dict(
            confirmation=form.kind == "email" and slot == "confirmation",
            receipt_block=confirmation_block(base.document(), campaign.pk),
        )
        before = sample_render(
            previous["values"] if previous else None,
            parish=parish,
            campaign=campaign.active_configuration.values,
            **receipt,
        )
        after = sample_render(
            values,
            parish=parish,
            campaign=campaign.active_configuration.values,
            **receipt,
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
        # Only an editor GET may ask to start from the default text.
        start = (
            request.method != "POST"
            and kind is not None
            and filters(request.GET, allowed={"start"}) == {"start": "default"}
        )
        if (request.GET and not start) or request.FILES:
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
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
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
                        and (
                            kind == "page"
                            or (slot == "confirmation" and revision_id is None)
                            or row["id"] == str(revision_id)
                        )
                    ),
                    None,
                )
                if revision_id and previous is None:
                    raise LookupError("Content revision is unavailable.")
                # "?start=default" only pre-fills the form: it starts an empty
                # slot or resets a configured one, and nothing changes until
                # the Admin previews and applies it like any other edit.
                initial = (
                    default_initial(kind, slot)
                    if start
                    else (previous["values"] if previous else {})
                    | {
                        "generate_text": text_is_generated(
                            previous["values"] if previous else None
                        )
                    }
                ) | {"base_digest": configuration.active_configuration.digest}
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
                    else _page(
                        request,
                        form,
                        campaign,
                        labels[slot],
                        default_url=None if start else request.path + "?start=default",
                        saved=previous is not None,
                        started=start,
                    )
                )
        # Recheck access after the observation ends, so a GET's read-only
        # snapshot cannot hide a revocation committed while it rendered.
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


@require_POST
def plain_text_preview(request):
    """Return what the server makes of posted HTML source; nothing saves.

    One response serves both live previews in the content editors (setup and
    campaign): the sanitized HTML the visual editor redraws from, the plain
    text shown read-only while "Generate plain text from HTML" is checked, and
    a plain-language list of markup the sanitizer removed. The browser never
    renders the raw source itself. Only the HTML field (and the CSRF token)
    is accepted, from a currently authorized Administrator, and the read-only
    authorization never renews the idle session.
    """
    try:
        if (
            set(request.POST) - {"html", "csrfmiddlewaretoken"}
            or len(request.POST.getlist("html")) != 1
        ):
            raise ValueError("Only one HTML value is accepted.")
        service = runtime()
        if not allows(
            authenticated_admin(request, store=service.store, read_only=True),
            Capability.CONFIGURE,
        ):
            raise PermissionError("Content access was revoked.")
        source = request.POST["html"]
        prepared = prepare_content(source)
        removed = removed_markup(source)
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
    ) as error:
        return error_response(error)
    response = JsonResponse(
        {"html": prepared.html, "text": prepared.text, "removed": removed}
    )
    response["Cache-Control"] = "no-store"
    return response
