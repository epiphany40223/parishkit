"""The report worker may read weekly inputs; scheduler/MAIL cannot read raw text."""


def add_weekly_grants(tables, columns, *, worker):
    """Add only capture projections, never follow-up mutations or all answers."""
    if not worker:
        return
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
