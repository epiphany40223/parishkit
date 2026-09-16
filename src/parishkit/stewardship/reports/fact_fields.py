"""Pure fact-demand vocabulary shared by ORM writes and offline SQL provisioning."""

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
