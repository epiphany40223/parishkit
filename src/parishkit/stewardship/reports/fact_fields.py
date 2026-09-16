"""Pure fact-demand vocabulary shared by ORM writes and offline SQL provisioning."""

from uuid import UUID, uuid5

REQUEST_UPDATE_FIELDS = (
    "requested_source",
    "requested_source_generation",
    "requested_submission_watermark",
    "requested_timezone_configuration",
    "requested_through_date",
    "pending_revision",
    "pending_first_at",
    "pending_last_at",
    "pending_due_at",
    "version",
)


def rebuild_execution_key(demand_id, revision):
    """Keep allocation and stale-retry admission bound to the same window key."""
    if not isinstance(demand_id, UUID) or type(revision) is not int or revision < 1:
        raise ValueError("A fact execution key requires a demand UUID and revision.")
    return uuid5(demand_id, f"report-facts:{revision}")
