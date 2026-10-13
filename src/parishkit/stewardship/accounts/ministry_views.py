"""Admin Ministry activity workflow through previewed, versioned YAML requests.

The Ministries page lists the catalog in the shared Admin table; selecting
rows and choosing Review activation or Review inactivation reviews one bulk
change, applied and followed in place under the table (#750).
"""

from uuid import UUID, uuid4

from django import forms
from django.core import signing
from django.core.paginator import InvalidPage
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_http_methods, require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.live_ministries import live_ministries_editable
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import (
    read_transaction,
    work_transaction,
)
from parishkit.stewardship.source.catalog_names import ministry_display_name
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.refusals import UserFacingStale
from parishkit.stewardship.web.tables import Sorting, paginate

from . import admin_navigation
from .admin_editing import (
    IN_PLACE_VIEWS,
    confirm,
    editable_configuration,
    error_response,
    form_action,
    in_place_follow,
    review_region,
    sign_preview,
)
from .admin_editing import (
    principal as admin_principal,
)
from .authentication import runtime
from .campaign_create_views import created_campaign_refresh
from .configuration_models import MinistryActivity
from .configuration_request_reads import receipt as configuration_receipt
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


def not_included(rows, active, campaign_link):
    """Selected Ministries that activation will not add to the campaign.

    Without a current campaign there is nothing to include them in, and
    inactivation never adds anything, so neither needs the explanation.
    """
    if not (campaign_link and active):
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


def campaign_ministries_link(configuration):
    """Where the current campaign's Ministry selections change, or None.

    Activity (offered to parishioners at all) and inclusion in the current
    campaign are separate settings; this page changes only activity, so it
    points Admins at the place that changes inclusion: Campaign Ministries
    while the campaign is live (it sits under Ministries), else Campaign
    settings' Ministry selections. Returns ``{"url", "label"}``.
    """
    if configuration.current_campaign_id is None:
        return None
    campaign = (
        Campaign.objects.select_related("active_configuration")
        .filter(pk=configuration.current_campaign_id)
        .first()
    )
    if live_ministries_editable(campaign, configuration.current_campaign_id):
        return {
            "url": reverse("admin:campaign_ministries"),
            "label": _("Campaign Ministries"),
        }
    return {
        "url": reverse("admin:campaign_settings") + "#ministry-selections",
        "label": _("Campaign settings → Ministry selections"),
    }


def _preview(request, state, principal):
    """Sign one exact bulk intent for confirmation; a preview changes nothing.

    Every selected Ministry not already in the requested state becomes one
    patch operation of a single configuration request, so the whole selection
    is applied (and audited) atomically or not at all. Rows already in that
    state are reported and left alone. Returns the review region's values
    (``review_region``'s ``review`` or a refusal ``message``) and the status
    the page answers with: the review and its refusals are shown in place,
    under the table (#750).
    """
    configuration, current, activity, catalog = state
    form = ActivityForm(request.POST, catalog=catalog)
    if not form.is_valid():
        return {
            "message": _("Select at least one Ministry from the list, then try again.")
        }, 400
    active = form.cleaned_data["active"] == "yes"
    chosen = set(form.cleaned_data["ministry_duid"])
    rows = [row for row in catalog if row["duid"] in chosen]
    changing = [row for row in rows if row["active"] != active]
    if not changing:
        # Review only a change (#921): nothing selected would change.
        return {
            "message": _(
                "The selected Ministries are already active, so there is "
                "nothing to change."
            )
            if active
            else _(
                "The selected Ministries are already inactive, so there is "
                "nothing to change."
            )
        }, 400
    if len(changing) > MAX_CHANGES:
        return {
            "message": _(
                "One change can include at most 100 Ministries. Select fewer "
                "Ministries and apply the change in several steps."
            )
        }, 400
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
    campaign_link = campaign_ministries_link(configuration)
    return {
        "review": {
            # Drawn inside settings-review.html, above its Apply.
            "template": "stewardship/ministry-review.html",
            "notes": [],
            "changing": changing,
            "not_included": not_included(rows, active, campaign_link),
            "campaign_link": campaign_link,
            "unchanged": [row for row in rows if row["active"] == active],
            "new_active": active,
            "preview": sign_preview(
                actor=principal,
                configuration=configuration,
                snapshot=current.snapshot_id,
                patch=patch,
                salt=SALT,
            ),
        }
    }, 200


# Every data column sorts on the server over the whole catalog (read in
# name order, so equal values keep that order); the selection column holds
# controls, not data. Activity and inclusion sort "on" before "off". The
# Ministry DUID is neither shown, sorted nor searched: staff know a Ministry
# by its name (#1123).
CATALOG_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["name"].casefold(),
        "active": lambda row: not row["active"],
        "included": lambda row: not row["included"],
    },
    default="name",
)


def _listing(request, configuration, catalog, selected, *, status=200, **region):
    """Render the filtered, paged Ministry table and its in-place review region.

    ``selected`` is the page's validated view (filters, sort and page);
    ``region`` is ``review_region``'s ``review``, ``receipt``, ``refusal`` or
    ``message``. The step indicator follows the region through edit, review
    and apply (#196), all on this page.
    """
    query, state = selected.get("q", ""), selected.get("state", "all")
    if len(query) > 200 or state not in {"all", "active", "inactive"}:
        raise ValueError("Invalid catalog filter.")
    rows = [
        row
        for row in catalog
        if query.casefold() in row["name"].casefold()
        and (state == "all" or row["active"] == (state == "active"))
    ]
    table = paginate(
        rows,
        selected,
        carry=(("q", query), ("state", state)),
        sorting=CATALOG_SORTING,
    )
    step = "review" if region.get("review") else "edit"
    if region.get("receipt"):
        step = "apply"
    admin_navigation.place(request, flow="change", step=step)
    response = render(
        request,
        "stewardship/ministries.html",
        {
            "table": table,
            "query": query,
            "state": state,
            "parish_name": configuration.active_configuration.parish.name,
            "campaign_link": campaign_ministries_link(configuration),
            # The status read hands the view on to the quiet refresh once
            # the change is applied (admin_editing.IN_PLACE_VIEWS).
            **review_region("ministries", None, view=selected, **region),
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


# The Ministries page's own view: its filters, sort and page, which a change
# keeps (one list, admin_editing.IN_PLACE_VIEWS).
VIEW_FIELDS = frozenset(IN_PLACE_VIEWS["ministries"])


@require_http_methods(["GET", "HEAD", "POST"])
def ministry_activity(request):
    """List/search activity, then review, apply and follow a change in place.

    Review, apply and the change's status all happen on this page (#750),
    in the review region under the table: the selection form's answer
    replaces only that region and the step indicator, so the reader's ticks,
    page and sort stay. A refused Apply (an out-of-date preview) is the page
    again (status 409) with the explanation there. The page's view (its
    filters, sort and page) is kept through Apply's answer and the quiet
    refresh once the change is applied; ``request`` names that change.
    """
    try:
        service = runtime()
        # Reading a change's status (the page's own quiet refresh once it is
        # applied) is passive, as Change status is: it never renews idle time.
        principal = admin_principal(
            request,
            service,
            passive=request.method != "POST" and "request" in request.GET,
        )
        selected = filters(request.GET, allowed={*VIEW_FIELDS, "request"})
        requested = selected.pop("request", None)
        region, status = {}, 200
        if request.method == "POST":
            action = form_action(
                request.POST,
                preview_fields={"ministry_duid", "active"},
                multiple_fields={"ministry_duid"},
            )
            if action == "confirm":
                try:
                    response = confirm(
                        request,
                        service,
                        principal,
                        salt=SALT,
                        current_scope=_scope,
                        in_place=True,
                        view=selected,
                    )
                    response["Cache-Control"] = "no-store"
                    return response
                except UserFacingStale as error:
                    region, status = {"refusal": error.refusal}, 409
            elif action != "preview":
                raise ValueError("Unknown configuration action.")
        with (
            read_transaction()
            if request.method in {"GET", "HEAD"}
            else work_transaction()
        ):
            state = _state(service)
            if request.method == "POST" and not region:
                region, status = _preview(request, state, principal)
            elif request.method != "POST" and requested is not None:
                region["receipt"] = configuration_receipt(
                    request, service, UUID(requested), principal
                )
            response = _listing(
                request, state[0], state[3], selected, status=status, **region
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
        LookupError,
        InvalidPage,
        StaleRecordError,
        signing.BadSignature,
    ) as error:
        return error_response(error)


@require_safe
def configuration_request(request, request_id):
    """Passive status reads show Applied only for a committed activation receipt.

    An in-place settings page (#532) polls this read for its own live status,
    naming itself with ``in_place``; the status it reads then carries the
    follow-up that refreshes that page once the change settles.
    """
    try:
        service = runtime()
        principal = admin_principal(request, service, passive=True)
        with transaction.atomic():
            receipt = configuration_receipt(request, service, request_id, principal)
            # Place the status under the page the change came from, when this
            # sign-in remembers it, as the last step of the edit flow (#196).
            origin = admin_navigation.change_origin(request, request_id)
            arguments, labels = dict(origin[1]) if origin else {}, {}
            if origin:
                # Name the integration a key or settings change came from.
                from .integration_views import integration_origin

                extra, labels = integration_origin(*origin)
                arguments |= extra
            admin_navigation.place(
                request,
                parent=origin[0] if origin else None,
                arguments=arguments,
                labels=labels,
                flow="change",
                step="apply",
            )
            response = render(
                request,
                "stewardship/configuration-request.html",
                {
                    "receipt": receipt,
                    "follow_url": in_place_follow(request.GET, receipt.request_id),
                    "refresh_key": created_campaign_refresh(receipt),
                },
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
