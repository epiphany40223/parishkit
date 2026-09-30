"""Admin Ministry activity workflow through previewed, versioned YAML requests."""

from uuid import uuid4

from django import forms
from django.core import signing
from django.core.paginator import InvalidPage
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.source.catalog_names import ministry_display_name
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from . import admin_navigation
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
# One configuration request holds at most 100 patch operations
# (request_patch._build_records), so one bulk change is bounded the same way.
MAX_CHANGES = 100


class ActivityForm(forms.Form):
    """Only current catalog IDs and one explicit binary action are browser inputs."""

    ministry_duid = forms.TypedMultipleChoiceField(coerce=int)
    active = forms.ChoiceField(choices=(("yes", "Active"), ("no", "Inactive")))

    def __init__(self, data, *, catalog):
        """Offer exactly the current catalog's DUIDs as selectable values."""
        super().__init__(data)
        self.fields["ministry_duid"].choices = [
            (str(row["duid"]), row["name"]) for row in catalog
        ]


def not_included(rows, active, campaign_url):
    """Selected Ministries that activation will not add to the campaign.

    Without a current campaign there is nothing to include them in, and
    inactivation never adds anything, so neither needs the explanation.
    """
    if not (campaign_url and active):
        return []
    return [row for row in rows if not row["included"]]


def current_catalog(document, current):
    """The promoted source usable as this configuration's Ministry catalog, or None.

    One definition for every page that offers the catalog: the applied
    ParishSoft integration must name the promoted source's organization and a
    snapshot must be promoted, else there is no catalog to offer.
    """
    integration = next(
        (
            row["values"]
            for row in document["sections"].get("integrations", [])
            if row["values"]["kind"] == "parishsoft"
        ),
        None,
    )
    if (
        integration is None
        or current is None
        or current.snapshot_id is None
        or str(current.organization_id) != integration["settings"]["organization_id"]
    ):
        return None
    return current


def _state(service):
    """Resolve one coherent config and one snapshot, without mixing catalog versions."""
    configuration = editable_configuration(service)
    document = configuration.active_configuration.canonical_document
    current = current_catalog(document, SourceCurrent.objects.first())
    if current is None:
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
            # Tolerates a blank, null or odd ParishSoft name (#341).
            "name": ministry_display_name(
                int(row.source_key), row.payload.payload.get("name")
            ),
            "active": activity.get(int(row.source_key), {})
            .get("values", {})
            .get("active", True),
            "included": int(row.source_key) in selected,
        }
        for row in records
    ]
    catalog.sort(key=lambda row: (row["name"].casefold(), row["duid"]))
    return configuration, current, activity, catalog


def campaign_ministries_url(configuration):
    """Campaign settings' Ministry selections for the current campaign, or None.

    Activity (offered to parishioners at all) and inclusion in the current
    campaign are separate settings; this page changes only activity, so it
    points Admins at the place that changes inclusion.
    """
    if configuration.current_campaign_id is None:
        return None
    return (
        reverse("admin:campaign_settings", args=[configuration.current_campaign_id])
        + "#ministry-selections"
    )


def _preview(request, service, principal):
    """Sign one exact bulk intent for confirmation; a preview changes nothing.

    Every selected Ministry not already in the requested state becomes one
    patch operation of a single configuration request, so the whole selection
    is applied (and audited) atomically or not at all. Rows already in that
    state are reported and left alone.
    """
    configuration, current, activity, catalog = _state(service)
    form = ActivityForm(request.POST, catalog=catalog)
    if not form.is_valid():
        return _listing(
            request,
            configuration,
            catalog,
            {},
            notice=_("Select at least one Ministry from the list, then try again."),
            status=400,
        )
    active = form.cleaned_data["active"] == "yes"
    chosen = set(form.cleaned_data["ministry_duid"])
    rows = [row for row in catalog if row["duid"] in chosen]
    changing = [row for row in rows if row["active"] != active]
    if len(changing) > MAX_CHANGES:
        return _listing(
            request,
            configuration,
            catalog,
            {},
            notice=_(
                "One change can include at most 100 Ministries. Select fewer "
                "Ministries and apply the change in several steps."
            ),
            status=400,
        )
    patch = []
    for row in changing:
        previous = activity.get(row["duid"])
        retained = (
            MinistryActivity.objects.filter(
                organization_id=current.organization_id, ministry_duid=row["duid"]
            )
            .values_list("record_id", flat=True)
            .first()
        )
        values = {"active": active}
        if previous is None:
            values.update(
                organization_id=current.organization_id, ministry_duid=row["duid"]
            )
        patch.append(
            {
                "operation": "update" if previous else "add",
                "section": "ministries",
                "id": previous["id"] if previous else str(retained or uuid4()),
                "values": values,
            }
        )
    duids = {row["duid"] for row in changing}
    assignments = [
        row["values"]
        for row in configuration.active_configuration.canonical_document["sections"][
            "login_rules"
        ]
        if row["values"].get("kind") == "assignment"
        and row["values"]["ministry_duid"] in duids
    ]
    token = (
        sign_preview(
            actor=principal,
            configuration=configuration,
            snapshot=current.snapshot_id,
            patch=patch,
            salt=SALT,
        )
        if patch
        else None
    )
    campaign_url = campaign_ministries_url(configuration)
    return render(
        request,
        "stewardship/ministry-preview.html",
        {
            "changing": changing,
            "not_included": not_included(rows, active, campaign_url),
            "campaign_url": campaign_url,
            "unchanged": [row for row in rows if row["active"] == active],
            "new_active": active,
            "preview": token,
            "seeded_count": sum(row["source"] == "chair-seed" for row in assignments),
            "manual_count": sum(row["source"] == "manual" for row in assignments),
        },
    )


# Every data column sorts on the server over the whole catalog (read in
# name order, so equal values keep that order); the selection column holds
# controls, not data. Activity and inclusion sort "on" before "off".
CATALOG_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["name"].casefold(),
        "duid": lambda row: row["duid"],
        "active": lambda row: not row["active"],
        "included": lambda row: not row["included"],
    },
    default="name",
)


def _listing(request, configuration, catalog, selected, *, notice=None, status=200):
    """Render the filtered, paged Ministry table (the shared Admin table)."""
    query, state = selected.get("q", ""), selected.get("state", "all")
    if len(query) > 200 or state not in {"all", "active", "inactive"}:
        raise ValueError("Invalid catalog filter.")
    rows = [
        row
        for row in catalog
        if (
            query.casefold() in row["name"].casefold()
            or (query.isdecimal() and query == str(row["duid"]))
        )
        and (state == "all" or row["active"] == (state == "active"))
    ]
    table = paginate(
        rows,
        selected,
        carry=(("q", query), ("state", state)),
        sorting=CATALOG_SORTING,
    )
    response = render(
        request,
        "stewardship/ministries.html",
        {
            "table": table,
            "query": query,
            "state": state,
            "notice": notice,
            "parish_name": configuration.active_configuration.parish.name,
            "campaign_url": campaign_ministries_url(configuration),
        },
        status=status,
    )
    if status != 200:
        response.stewardship_safe_error = True
    return response


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
                request.POST,
                preview_fields={"ministry_duid", "active"},
                multiple_fields={"ministry_duid"},
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
            selected = filters(request.GET, allowed={"q", "state", *table_parameters()})
            response = _listing(request, configuration, catalog, selected)
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
            # Place the status under the page the change came from, when this
            # sign-in remembers it, as the last step of the edit flow (#196).
            origin = admin_navigation.change_origin(request, request_id)
            admin_navigation.place(
                request,
                parent=origin[0] if origin else None,
                arguments=origin[1] if origin else {},
                flow="change",
                step="apply",
            )
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
