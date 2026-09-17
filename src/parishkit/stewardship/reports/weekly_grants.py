"""The report worker may read weekly inputs; scheduler/MAIL cannot read raw text."""


def add_weekly_grants(tables, columns, *, worker):
    """Add only capture projections, never follow-up mutations or all answers."""
    tables.setdefault("stewardship_weekly_digest_completion_ready", set()).add("SELECT")
    tables.setdefault("stewardship_weekly_digest_preparation", set()).update(
        {"SELECT", "UPDATE" if worker else "INSERT"}
    )
    for table, fields in {
        "stewardship_weekly_digest_snapshot": {
            "id",
            "preparation_id",
            "submission_watermark",
            "information",
            "corrections",
        },
        "stewardship_weekly_digest_recipient": {"id", "snapshot_id", "outbox_id"},
    }.items():
        columns.setdefault(table, {}).setdefault("SELECT", set()).update(fields)
    if not worker:
        return
    tables.setdefault("stewardship_weekly_digest_snapshot", set()).update(
        {"SELECT", "INSERT"}
    )
    tables.setdefault("stewardship_weekly_digest_recipient", set()).update(
        {"SELECT", "INSERT"}
    )
    for table, fields in {
        "stewardship_additional_information": {
            "id",
            "submission_id",
            "disposition",
            "text",
        },
        "stewardship_submission": {
            "id",
            "family_id",
            "campaign_id",
            "mode",
            "campaign_sequence",
            "submitted_at",
        },
    }.items():
        columns.setdefault(table, {}).setdefault("SELECT", set()).update(fields)
