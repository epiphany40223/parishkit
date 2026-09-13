"""Archived-campaign cloning through one reviewed, immutable configuration intent."""

from uuid import UUID, uuid4

from django.core import signing
from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import confirm, error_response, principal, sign_preview
from .authentication import runtime
from .campaign_cloning import clone_initial, clone_patch, clone_structures
from .campaign_forms import CampaignForm
from .campaign_preview import describe_changes
from .campaign_views import MULTIPLE_FIELDS, _catalog, _scope, _state, _target
from .content_forms import EMAIL_LABELS, PAGE_LABELS, sample_render
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .request_patch import build_candidate
from .schedule_forms import Schedules, schedule_action
from .schedule_views import _describe
from .sessions import authenticated_admin


def _seed(request, actor, configuration, salt):
    """Bind stable new IDs to this Admin, base configuration and archived source."""
    digest = configuration.active_configuration.digest
    if request.method == "POST":
        token = request.POST.get("clone_seed", "")
        if len(token) > 2048:
            raise ValueError("Invalid clone seed.")
        try:
            seed = signing.loads(token, salt=salt, max_age=900)
        except signing.SignatureExpired:
            raise StaleRecordError(
                "Reload the expired clone before editing it."
            ) from None
        if seed["actor"] != str(actor.identity):
            raise PermissionError("Clone belongs to another Administrator.")
        if seed["base"] != digest:
            raise StaleRecordError("Reload the clone before editing it.")
        return UUID(seed["target"]), token
    target = uuid4()
    return target, signing.dumps(
        {"actor": str(actor.identity), "base": digest, "target": str(target)}, salt=salt
    )


def _page(request, source, form, schedules, seed, *, status=200):
    """Render unapplied structures with all new civil dates intentionally empty."""
    response = render(
        request,
        "stewardship/clone-settings.html",
        {"source": source, "form": form, "schedules": schedules, "clone_seed": seed},
        status=status,
    )
    if status == 400:
        response.stewardship_safe_error = True
    return response


def _preview(
    request, service, actor, state, source, target, form, schedules, content, seed, salt
):
    """Validate the successor and disclose every copied content/share/mail value."""
    valid = form.is_valid()
    if valid:
        schedules.campaign = form.values()
    schedules_valid = schedules.is_valid()
    if not valid or not schedules_valid:
        return _page(request, source, form, schedules, seed, status=400)
    configuration, fingerprint = state[0], state[-1]
    values = form.values()
    if form.cleaned_data["base_digest"] != configuration.active_configuration.digest:
        raise StaleRecordError("Reload the clone before editing it.")
    parish = configuration.active_configuration.canonical_document["sections"][
        "parish"
    ][0]["values"]
    if values["timezone"] != parish["timezone"]:
        form.add_error("timezone", "A new campaign must start in the Parish timezone.")
        return _page(request, source, form, schedules, seed, status=400)
    base = service.store.active()
    if base is None or base.digest != configuration.active_configuration.digest:
        raise StaleRecordError("The applied configuration changed.")
    try:
        patch = clone_patch(target, values, content, schedules)
        build_candidate(base, patch, candidate_id=uuid4())
        token = sign_preview(
            actor=actor,
            configuration=configuration,
            patch=patch,
            salt=salt,
            snapshot=fingerprint,
        )
        if len(token) > 256_000:
            raise ValueError("The clone preview exceeds its bounded intent size.")
        previews = [
            {
                "label": (
                    PAGE_LABELS if row["values"]["kind"] == "page" else EMAIL_LABELS
                )[row["values"]["slot"]],
                "rendered": sample_render(
                    row["values"], parish=parish, campaign=values
                ),
            }
            for row in content
        ]
    except (ConfigError, ValueError):
        form.add_error(
            None,
            "Check the new name, dates and schedules. Reminders must follow one "
            "initial mailing. Clones are limited to 100 configuration records and "
            "the preview size limit; larger campaigns can start empty and add "
            "content in smaller requests.",
        )
        return _page(request, source, form, schedules, seed, status=400)
    return render(
        request,
        "stewardship/clone-preview.html",
        {
            "source": source,
            "preview": token,
            "content_previews": previews,
            "changes": describe_changes(
                {},
                values,
                ministries=form.fields["ministry_duids"].choices,
                funds=form.fields["fund_duids"].choices,
            ),
            "schedules": [
                _describe(row["values"])
                for row in patch
                if row["section"] == "schedules"
            ],
        },
    )


@require_http_methods(["GET", "HEAD", "POST"])
def campaign_clone(request, campaign_id):
    """Only an Admin with no current campaign may clone an archived configuration."""
    try:
        service = runtime()
        actor = principal(request, service)
        salt = f"stewardship-campaign-clone-v1:{campaign_id}"
        filters(request.GET, allowed=set())
        if request.FILES:
            raise ValueError("Clone uploads are not supported.")
        if request.method == "POST":
            action = schedule_action(
                request.POST,
                window_fields=set(),
                extra_fields={*CampaignForm.base_fields, "clone_seed"},
                multiple_fields=MULTIPLE_FIELDS,
            )
            if action == "confirm":
                return confirm(request, service, actor, salt=salt, current_scope=_scope)
        with work_transaction():
            state = _state(service)
            configuration, campaigns, current_source, held, _ = state
            _target(configuration, campaigns, held, None)
            source = next((row for row in campaigns if row.pk == campaign_id), None)
            if source is None or source.state != "archived":
                raise LookupError("An archived source campaign is required.")
            document = configuration.active_configuration.canonical_document
            source_record = {
                "id": str(source.pk),
                "values": source.active_configuration.values,
            }
            target, seed = _seed(request, actor, configuration, salt + ":seed")
            previous, content, rows = clone_structures(document, source_record, target)
            ministries, funds = _catalog(configuration, current_source, {})
            initial = clone_initial(
                source_record,
                digest=configuration.active_configuration.digest,
                timezone=configuration.active_configuration.parish.timezone,
                ministries=ministries,
            )
            data = request.POST if request.method == "POST" else None
            form = CampaignForm(
                data,
                initial=initial,
                previous=previous,
                ministries=ministries,
                funds=funds,
            )
            schedules = Schedules(
                data,
                prefix="schedules",
                templates=content,
                previous=rows,
                campaign_id=target,
                campaign=source.active_configuration.values,
            )
            response = (
                _preview(
                    request,
                    service,
                    actor,
                    state,
                    source,
                    target,
                    form,
                    schedules,
                    content,
                    seed,
                    salt,
                )
                if request.method == "POST"
                else _page(request, source, form, schedules, seed)
            )
            if not allows(
                authenticated_admin(request, store=service.store, read_only=True),
                Capability.CONFIGURE,
            ):
                raise PermissionError("Clone access was revoked.")
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
