"""Installer preflight for the admitted Testing-only campaign storage subset.

These checks produce terminal invalid-candidate receipts before manifest changes.
SQL repeats the transactional invariants. The serialized installer is currently
the only runtime writer; future lifecycle/purge integration must join its global
admission lock and extend this preflight rather than open parallel unsafe paths.
"""

from parishkit.config import ConfigError


def validate_installation(document):
    """Reject unsupported draft operations without changing YAML or runtime state."""
    from parishkit.stewardship.accounts.runtime_models import SystemConfiguration

    from .models import Campaign, ScheduleRevision

    records = document["sections"].get("campaigns", [])
    runtime = SystemConfiguration.objects.first()
    target = records[0] if len(records) == 1 else None
    current = runtime.current_campaign_id if runtime is not None else None
    if len(records) > 1 or (
        current is not None and (target is None or str(current) != target["id"])
    ):
        raise ConfigError("Only the current draft campaign can be configured.")
    if target is not None:
        if runtime is not None and (
            runtime.mode != "testing" or runtime.restore_review_required
        ):
            raise ConfigError("Campaign configuration is not currently admitted.")
        if current is None and (
            Campaign.objects.exists()
            or target["values"]["timezone"]
            != document["sections"]["parish"][0]["values"]["timezone"]
        ):
            raise ConfigError("A new draft must copy the current parish timezone.")
    # Check retired definition identities before immutable projection insertion;
    # otherwise the SQL defense would leave the installer at 'validating'.
    schedules = document["sections"].get("schedules", [])
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
