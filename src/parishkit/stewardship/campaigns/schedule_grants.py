"""Metadata-only Family planning grants, without mail execution or private reads."""


def add_schedule_planning_grants(tables, columns):
    """Permit bounded allocation/coverage and only unallocated pending transitions."""
    for table in (
        "stewardship_schedule_definition",
        "stewardship_schedule_revision",
        "stewardship_schedule_occurrence",
        "stewardship_occurrence_transition",
        "stewardship_schedule_fulfillment",
        "stewardship_restore_delivery_hold",
    ):
        tables.setdefault(table, set()).add("SELECT")
    for table in (
        "stewardship_schedule_occurrence",
        "stewardship_occurrence_transition",
        "stewardship_schedule_fulfillment",
    ):
        tables[table].add("INSERT")
    columns.setdefault("stewardship_schedule_definition", {}).setdefault(
        "UPDATE", set()
    ).add("id")
    columns.setdefault("stewardship_schedule_occurrence", {}).setdefault(
        "UPDATE", set()
    ).update(
        {
            "state",
            "reason",
            "replacement_id",
            "version",
            "actor_id",
            "correlation_id",
        }
    )
    columns.setdefault("stewardship_family_campaign", {}).setdefault(
        "SELECT", set()
    ).update(
        {
            "id",
            "campaign_id",
            "active",
            "email_eligible",
            "email_deliverable",
            "effective_submission_id",
        }
    )
    columns.setdefault("stewardship_submission", {}).setdefault("SELECT", set()).update(
        {
            "family_id",
            "mode",
            "rehearsal_epoch_id",
        }
    )
