"""First-campaign preparation from this original setup's unpublished catalogs."""

from dataclasses import dataclass
from uuid import UUID

from django.db.models import F
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.catalog_names import (
    fund_display_name,
    ministry_display_name,
)
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry
from parishkit.stewardship.web.refusals import UserFacingMissing

from .sessions import database_now
from .setup_drafts import _owned
from .setup_exchange_models import SetupSourceResult
from .setup_models import SetupDraftSection
from .setup_staging import _expiry


@dataclass(frozen=True)
class SetupCatalog:
    """Only the original owner's ready result supplies displayed source choices."""

    result_id: UUID
    timezone: str
    ministries: tuple
    funds: tuple


def _source_result(attempt):
    """The attempt's exact, still-valid staged load result, or None."""
    return (
        SetupSourceResult.objects.filter(
            exchange__attempt=attempt,
            exchange__scrubbed_at=None,
            exchange__task__root_id=attempt.source_task_id,
            exchange__task__state="succeeded",
            exchange__task_fence=F("exchange__task__fence"),
            exchange__credential__scrubbed_at=None,
            exchange__credential_version=F("exchange__credential__version"),
            exchange__fingerprint=F("exchange__credential__fingerprint"),
            snapshot__state="ready",
            snapshot__task_id=F("exchange__task_id"),
            snapshot__source_fence=F("exchange__source_fence"),
        )
        .order_by("-snapshot__completed_at", "-pk")
        .first()
    )


def staged_source_result(attempt_id):
    """The id of the attempt's exact staged load result, for final readiness.

    System setup (#142) has no campaign section to carry it, so final
    confirmation reads it here; the readiness guard checks it again in SQL.
    Call inside the caller's work transaction.
    """
    from .setup_models import SetupAttempt

    result = _source_result(SetupAttempt.objects.get(pk=attempt_id))
    if result is None:
        raise ValueError("Load the parish data before final confirmation.")
    return result.pk


def campaign_catalog(request, service, attempt_id):
    """No global/current corpus or another login can substitute for staged truth."""
    with work_transaction():
        attempt = _owned(request, service, attempt_id)[1]
        if (
            attempt.state != "collecting"
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("First-campaign preparation is unavailable.")
        result = _source_result(attempt)
        if result is None:
            raise UserFacingMissing(
                _("Load the parish data before choosing the first campaign."),
                link=reverse("admin:setup_source"),
                link_label=_("Go to “Load parish data”"),
            )
        profile = SetupDraftSection.objects.values_list("values", flat=True).get(
            attempt=attempt, step="parish", scrubbed_at=None
        )
        choices = []
        for model in (SnapshotMinistry, SnapshotFund):
            entries = []
            for row in model.objects.filter(
                snapshot_id=result.snapshot_id
            ).select_related("payload"):
                payload = row.payload.payload
                # Upstream Ministry active flags are deliberately not authoritative.
                # First setup has no local overrides; later activity uses its editor.
                if model is SnapshotFund and payload.get("active", True) is False:
                    continue
                duid = int(row.source_key)
                # A blank, null or odd ParishSoft name must not break setup.
                name = (
                    ministry_display_name
                    if model is SnapshotMinistry
                    else fund_display_name
                )(duid, payload.get("name"))
                entries.append((str(duid), name))
            entries.sort(key=lambda row: (row[1].casefold(), int(row[0])))
            choices.append(tuple(entries))
        return SetupCatalog(result.pk, profile["timezone"], *choices)


def admit_campaign_values(request, service, attempt_id, values):
    """Repeat exact result, source selection and initial Parish timezone at save."""
    catalog = campaign_catalog(request, service, attempt_id)
    campaign = values["campaign"]
    selected_funds = set()
    if campaign["financial"]:
        selected_funds = set(campaign["financial"]["fund_duids"]) | set(
            campaign["financial"]["comparison_fund_duids"]
        )
    if (
        values["source_result"] != str(catalog.result_id)
        or campaign["timezone"] != catalog.timezone
        or set(campaign["ministry_duids"]) - {int(key) for key, _ in catalog.ministries}
        or selected_funds - {int(key) for key, _ in catalog.funds}
    ):
        raise ValueError("First-campaign selections differ from the setup catalog.")
