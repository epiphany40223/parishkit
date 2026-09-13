"""First-campaign preparation from this original setup's unpublished catalogs."""

from dataclasses import dataclass
from uuid import UUID

from django.db.models import F

from parishkit.stewardship.campaigns.work_locks import work_transaction
from parishkit.stewardship.source.version_models import SnapshotFund, SnapshotMinistry

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


def campaign_catalog(request, service, attempt_id):
    """No global/current corpus or another login can substitute for staged truth."""
    with work_transaction():
        _, attempt = _owned(request, service, attempt_id)
        if (
            attempt.state != "collecting"
            or _expiry(attempt, database_now()) is not None
        ):
            raise PermissionError("First-campaign preparation is unavailable.")
        result = SetupSourceResult.objects.filter(
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
        ).first()
        if result is None:
            raise LookupError("Complete this setup's source load first.")
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
                entries.append((str(int(row.source_key)), payload["name"]))
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
