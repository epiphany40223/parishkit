"""Admin Ministry activity workflow through previewed, versioned YAML requests."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.core.paginator import InvalidPage, Paginator
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import (
    expected_version,
    filters,
)

from .admin_editing import (
    confirm,
    editable_configuration,
    error_response,
    form_action,
    sign_preview,
)
from .admin_editing import (
    principal as admin_principal,
)
from .authentication import runtime
from .configuration_models import MinistryActivity
from .configuration_requests import request_status
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import authenticated_admin

SALT = "stewardship-ministry-activity-preview-v1"


class ActivityForm(forms.Form):
    """Only a current catalog ID and explicit binary action are browser inputs."""

    ministry_duid = forms.IntegerField(min_value=1, max_value=2**31 - 1)
    active = forms.ChoiceField(choices=(("yes", "Active"), ("no", "Inactive")))


def _state(service):
    """Resolve one coherent config and one snapshot, without mixing catalog versions."""
    configuration = editable_configuration(service)
    document = configuration.active_configuration.canonical_document
    integration = next(
        (
            row["values"]
            for row in document["sections"].get("integrations", [])
            if row["values"]["kind"] == "parishsoft"
        ),
        None,
    )
    current = SourceCurrent.objects.first()
    if (
        integration is None
        or current is None
        or current.snapshot_id is None
        or str(current.organization_id) != integration["settings"]["organization_id"]
    ):
        raise ConfigError("A current Ministry catalog is required.")
    records = list(
        SnapshotMinistry.objects.filter(snapshot_id=current.snapshot_id)
        .select_related("payload")
        .order_by("source_key")
    )
    activity = {
        row["values"]["ministry_duid"]: row
        for row in document["sections"].get("ministries", [])
        if row["values"]["organization_id"] == current.organization_id
    }
    campaign = next(
        (
            row["values"]
            for row in document["sections"].get("campaigns", [])
            if row["id"] == str(configuration.current_campaign_id)
        ),
        None,
    )
    selected = set(campaign["ministry_duids"]) if campaign else set()
    catalog = [
        {
            "duid": int(row.source_key),
            "name": row.payload.payload["name"],
            "active": activity.get(int(row.source_key), {})
            .get("values", {})
            .get("active", True),
            "included": int(row.source_key) in selected,
        }
        for row in records
    ]
    catalog.sort(key=lambda row: (row["name"].casefold(), row["duid"]))
    return configuration, current, activity, catalog


def _preview(request, service, principal):
    """Sign exact scope/intent for confirmation; preview creates no durable change."""
    form = ActivityForm(request.POST)
    if not form.is_valid():
        raise ValueError("Invalid Ministry activity form.")
    configuration, current, activity, catalog = _state(service)
    duid, active = (
        form.cleaned_data["ministry_duid"],
        form.cleaned_data["active"] == "yes",
    )
    row = next((item for item in catalog if item["duid"] == duid), None)
    if row is None or row["active"] == active:
        raise StaleRecordError("Reload the Ministry catalog before changing activity.")
    previous = activity.get(duid)
    retained = (
        MinistryActivity.objects.filter(
            organization_id=current.organization_id, ministry_duid=duid
        )
        .values_list("record_id", flat=True)
        .first()
    )
    identifier = previous["id"] if previous else str(retained or uuid4())
    values = {"active": active}
    if previous is None:
        values.update(organization_id=current.organization_id, ministry_duid=duid)
    patch = [
        {
            "operation": "update" if previous else "add",
            "section": "ministries",
            "id": identifier,
            "values": values,
        }
    ]
    policy = configuration.active_configuration.canonical_document["sections"][
        "login_rules"
    ]
    assignments = [
        row["values"]
        for row in policy
        if row["values"].get("kind") == "assignment"
        and row["values"]["ministry_duid"] == duid
    ]
    token = sign_preview(
        actor=principal,
        configuration=configuration,
        snapshot=current.snapshot_id,
        patch=patch,
        salt=SALT,
    )
    return render(
        request,
        "stewardship/ministry-preview.html",
        {
            "ministry": row,
            "new_active": active,
            "preview": token,
            "seeded_count": sum(row["source"] == "chair-seed" for row in assignments),
            "manual_count": sum(row["source"] == "manual" for row in assignments),
        },
    )


def _scope(service):
    """Pin the catalog snapshot as well as YAML for a Ministry impact confirmation."""
    configuration, current, _, _ = _state(service)
    return configuration, current.snapshot_id


@require_http_methods(["GET", "HEAD", "POST"])
def ministry_activity(request):
    """List/search activity, preview impact, and submit an exact versioned request."""
    try:
        service = runtime()
        principal = admin_principal(request, service)
        if request.method == "POST":
            action = form_action(
                request.POST, preview_fields={"ministry_duid", "active"}
            )
            if action == "preview":
                response = _preview(request, service, principal)
            elif action == "confirm":
                response = confirm(
                    request, service, principal, salt=SALT, current_scope=_scope
                )
            else:
                raise ValueError("Unknown configuration action.")
        else:
            configuration, _, _, catalog = _state(service)
            selected = filters(request.GET, allowed={"q", "state", "page"})
            query, state = selected.get("q", ""), selected.get("state", "all")
            if len(query) > 200 or state not in {"all", "active", "inactive"}:
                raise ValueError("Invalid catalog filter.")
            catalog = [
                row
                for row in catalog
                if query.casefold() in row["name"].casefold()
                and (state == "all" or row["active"] == (state == "active"))
            ]
            page = Paginator(catalog, 50).page(
                expected_version(selected.get("page", "1"))
            )
            response = render(
                request,
                "stewardship/ministries.html",
                {
                    "ministries": page.object_list,
                    "page": page,
                    "query": query,
                    "state": state,
                    "parish_name": configuration.active_configuration.parish.name,
                },
            )
        fresh = authenticated_admin(request, store=service.store, read_only=True)
        if not allows(fresh, Capability.CONFIGURE):
            raise PermissionError("Configuration access was revoked.")
        response["Cache-Control"] = "no-store"
        return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        InvalidPage,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)


@require_safe
def configuration_request(request, request_id):
    """Passive status reads show Applied only for a committed activation receipt."""
    try:
        service = runtime()
        principal = admin_principal(request, service, passive=True)
        with transaction.atomic():
            receipt = request_status(request_id=request_id, actor_id=principal.identity)
            if not allows(
                authenticated_admin(request, store=service.store, read_only=True),
                Capability.CONFIGURE,
            ):
                raise PermissionError("Configuration access was revoked.")
            response = render(
                request, "stewardship/configuration-request.html", {"receipt": receipt}
            )
            response["Cache-Control"] = "no-store"
            return response
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
    ) as error:
        return error_response(error)
