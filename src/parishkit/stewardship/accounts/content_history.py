"""Read-only fictional previews pinned to a campaign's retained configuration.

The campaign's selected projection, not the latest global Parish configuration,
owns its historical content and branding. This page grants no editing, sending,
live Family lookup or selection of arbitrary prepared YAML versions.
"""

from django.db import DatabaseError
from django.shortcuts import render
from django.views.decorators.http import require_http_methods

from parishkit.config import ConfigError
from parishkit.stewardship.campaigns.models import Campaign
from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.storage import StaleRecordError
from parishkit.stewardship.web.contracts import filters

from .admin_editing import editable_configuration, error_response, principal
from .authentication import runtime
from .branding_context import branding_for
from .content_forms import EMAIL_LABELS, PAGE_LABELS, sample_render
from .integration_views import _checked
from .limiting import LimiterUnavailable
from .runtime_models import ConfigurationActivation


@require_http_methods(["GET", "HEAD"])
def content_history(request, campaign_id, revision_id=None):
    """Browse retained selected content with the same final Admin/session recheck."""
    try:
        service = runtime()
        principal(request, service)
        filters(request.GET, allowed=set())
        with work_transaction():
            editable_configuration(service)
            campaign = (
                Campaign.objects.select_related(
                    "active_configuration__configuration__parish"
                )
                .filter(pk=campaign_id)
                .first()
            )
            if campaign is None or campaign.state in {
                "purging",
                "purge_cleanup_failed",
                "purged",
            }:
                raise LookupError("Retained campaign content is unavailable.")
            version = campaign.active_configuration.configuration
            if not ConfigurationActivation.objects.filter(
                configuration=version
            ).exists():
                raise LookupError("Retained campaign configuration is unavailable.")
            sections = version.canonical_document["sections"]
            records = [
                row
                for row in sections.get("content", [])
                if row["values"]["campaign_id"] == str(campaign.pk)
            ]
            records.sort(
                key=lambda row: (
                    row["values"]["kind"],
                    row["values"]["slot"],
                    row["values"]["subject"] or "",
                    row["id"],
                )
            )
            entries = [
                {
                    "id": row["id"],
                    "label": (
                        PAGE_LABELS if row["values"]["kind"] == "page" else EMAIL_LABELS
                    )[row["values"]["slot"]],
                    "subject": row["values"]["subject"],
                }
                for row in records
            ]
            selected = next(
                (row for row in records if row["id"] == str(revision_id)), None
            )
            if revision_id is not None and selected is None:
                raise LookupError("Retained content revision is unavailable.")
            sample = sample_render(
                selected["values"] if selected else None,
                parish=sections["parish"][0]["values"],
                campaign=campaign.active_configuration.values,
            )
            response = render(
                request,
                "stewardship/content-history.html",
                {
                    "campaign": campaign,
                    "version": version,
                    "entries": entries,
                    "selected": selected,
                    "sample": sample,
                    "parish_branding": branding_for(version.parish),
                },
            )
            return _checked(request, service, response)
    except (
        ConfigError,
        DatabaseError,
        LimiterUnavailable,
        PermissionError,
        ValueError,
        LookupError,
        StaleRecordError,
    ) as error:
        return error_response(error)
