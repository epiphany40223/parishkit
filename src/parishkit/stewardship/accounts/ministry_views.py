"""The Admin Ministries page: the ParishSoft Ministry catalog, read only.

It lists the promoted catalog's Ministries in the shared Admin table, with
whether each is in the current campaign, and points to Campaign settings,
where a campaign's Ministries are chosen. There is no local active/inactive
override any more (#1267): every Ministry ParishSoft lists can be chosen.
This module also holds Change status, the shared configuration request page.
"""

from django.core.paginator import InvalidPage
from django.db import DatabaseError, transaction
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.decorators.http import require_safe

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.work_locks import read_transaction
from parishkit.stewardship.source.catalog_names import ministry_display_name
from parishkit.stewardship.source.snapshot_models import SourceCurrent
from parishkit.stewardship.source.version_models import SnapshotMinistry
from parishkit.stewardship.web.contracts import filters
from parishkit.stewardship.web.tables import Sorting, paginate, table_parameters

from . import admin_navigation
from .admin_editing import (
    editable_configuration,
    error_response,
    in_place_follow,
)
from .admin_editing import (
    principal as admin_principal,
)
from .authentication import runtime
from .campaign_create_views import created_campaign_refresh
from .configuration_request_reads import receipt as configuration_receipt
from .limiting import LimiterUnavailable
from .policy import Capability, allows
from .sessions import authenticated_admin


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
    """One coherent configuration and its catalog, from one snapshot.

    Returns the editable configuration, the promoted source current row
    (``SourceCurrent``) and the catalog rows, each
    ``{"duid", "name", "included"}`` (in the current campaign), sorted by
    name. Without a promoted catalog for the configured organization the
    page has nothing to show and says so.
    """
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
            "included": int(row.source_key) in selected,
        }
        for row in records
    ]
    catalog.sort(key=lambda row: (row["name"].casefold(), row["duid"]))
    return configuration, current, catalog


def campaign_ministries_link(configuration):
    """Where the current campaign's Ministry selections change, or None.

    This page only lists the catalog, so it points Admins at the place that
    chooses a campaign's Ministries: Campaign settings' Ministry selections,
    a draft's or a live campaign's alike (#1119). Returns ``{"url", "label"}``.
    """
    if configuration.current_campaign_id is None:
        return None
    return {
        "url": reverse("admin:campaign_settings") + "#ministry-selections",
        "label": _("Campaign settings → Ministry selections"),
    }


# Every column sorts on the server over the whole catalog (read in name
# order, so equal values keep that order). Inclusion sorts "on" before
# "off". The Ministry DUID is neither shown, sorted nor searched: staff know
# a Ministry by its name (#1123).
CATALOG_SORTING = Sorting.by_column(
    {
        "name": lambda row: row["name"].casefold(),
        "included": lambda row: not row["included"],
    },
    default="name",
)
# The page's view: its name search, sort and page. Anything else, such as
# the retired activity filter or a change's ``request``, is refused.
VIEW_FIELDS = frozenset({"q", *table_parameters()})


@require_safe
def ministries(request):
    """List and search the Ministry catalog; this page changes nothing.

    The view (search, sort and page) is validated like any other address;
    the access recheck after rendering refuses a page whose Administrator
    lost configuration access meanwhile.
    """
    try:
        service = runtime()
        admin_principal(request, service)
        selected = filters(request.GET, allowed=VIEW_FIELDS)
        query = selected.get("q", "")
        if len(query) > 200:
            raise ValueError("Invalid catalog filter.")
        with read_transaction():
            configuration, _, catalog = _state(service)
            rows = [
                row for row in catalog if query.casefold() in row["name"].casefold()
            ]
            response = render(
                request,
                "stewardship/ministries.html",
                {
                    "table": paginate(
                        rows, selected, carry=(("q", query),), sorting=CATALOG_SORTING
                    ),
                    "query": query,
                    "campaign_link": campaign_ministries_link(configuration),
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
        LookupError,
        InvalidPage,
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
