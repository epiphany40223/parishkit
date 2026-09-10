"""Installer preflight for campaign configuration and safe schedule replacement.

Invalid intent produces terminal receipts; temporary admission gates stay retryable.
SQL repeats the transactional invariants. The serialized installer is currently
the only configuration writer; lifecycle/purge decisions join the same lock.
"""

from parishkit.config import ConfigError


class CampaignAdmissionUnavailable(RuntimeError):
    """Deployment state temporarily blocks configuration; this is not invalid intent."""


def validate_installation(document, *, request_id=None):
    """Reject unsupported draft operations without changing YAML or runtime state."""
    from django.db.models import Q

    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .models import (
        Campaign,
        CampaignConfigurationIntent,
        CampaignWorkGate,
        ScheduleDefinition,
        ScheduleRevision,
    )

    records = document["sections"].get("campaigns", [])
    runtime = SystemConfiguration.objects.first()
    candidates = {row["id"]: row for row in records}
    existing = {
        str(row.pk): row
        for row in Campaign.objects.select_related("active_configuration")
    }
    current = runtime.current_campaign_id if runtime is not None else None
    intent = (
        CampaignConfigurationIntent.objects.filter(request_id=request_id).first()
        if request_id
        else None
    )
    added = set(candidates) - set(existing)
    if len(added) > 1 or (current is not None and added):
        raise ConfigError("Only one current campaign can be configured.")
    for identifier, row in existing.items():
        if identifier not in candidates:
            raise ConfigError("Campaign history cannot be removed.")
        values = candidates[identifier]["values"]
        if identifier != str(current) and values != row.active_configuration.values:
            raise ConfigError("Historical campaign configuration cannot be edited.")
        if row.structural_locked and any(
            values[key] != row.active_configuration.values[key]
            for key in values
            if key
            not in (
                {"name", "year_label", "content_versions", "end_date"}
                if intent and intent.campaign_id == row.pk
                else {"name", "year_label", "content_versions"}
            )
        ):
            raise ConfigError("Campaign structural settings are locked.")
    target = (
        candidates.get(str(current))
        if current
        else (candidates[next(iter(added))] if added else None)
    )
    if target is not None:
        changed = (
            target["id"] not in existing
            or target["values"] != existing[target["id"]].active_configuration.values
        )
        if runtime is not None and runtime.restore_review_required and changed:
            raise CampaignAdmissionUnavailable(
                "Campaign configuration is not currently admitted."
            )
        if current is None and (
            (runtime is not None and runtime.mode != "testing")
            or any(row.state not in {"archived", "purged"} for row in existing.values())
            or CampaignWorkGate.objects.filter(
                state__in=["preparing", "running"]
            ).exists()
            or (runtime is not None and runtime.restore_review_required)
            or target["values"]["timezone"]
            != document["sections"]["parish"][0]["values"]["timezone"]
        ):
            raise ConfigError("A new draft must copy the current parish timezone.")
    # Check retired definition identities before immutable projection insertion;
    # otherwise the SQL defense would leave the installer at 'validating'.
    schedules = document["sections"].get("schedules", [])
    proposed_schedules = {row["id"]: row["values"] for row in schedules}
    for definition in ScheduleDefinition.objects.select_related("current_revision"):
        proposed = proposed_schedules.get(str(definition.pk))
        old = (
            definition.current_revision.values if definition.current_revision else None
        )
        if proposed == old:
            continue
        if runtime is not None and runtime.restore_review_required:
            raise CampaignAdmissionUnavailable(
                "Schedule changes are held for restore review."
            )
        if (
            definition.scheduleoccurrence_set.filter(
                revision=definition.current_revision
            )
            .filter(
                Q(state__in=["running", "delivery_unknown"])
                | (
                    Q(state="pending")
                    & (
                        Q(outbox_id__isnull=False)
                        | Q(
                            task__state__in=[
                                "queued",
                                "running",
                                "retry_wait",
                                "abandoned",
                            ]
                        )
                    )
                )
            )
            .exists()
        ):
            raise CampaignAdmissionUnavailable(
                "Schedule replacement must wait for in-flight work."
            )
    identities = {
        row["id"]: (row["values"]["campaign_id"], row["values"]["kind"])
        for row in schedules
    }
    for identifier, campaign_id, kind in (
        ScheduleRevision.objects.filter(record_id__in=identities)
        .values_list("record_id", "campaign_id", "kind")
        .distinct()
    ):
        if identities[str(identifier)] != (str(campaign_id), kind):
            raise ConfigError("Logical schedule identities cannot be repurposed.")
