"""Explicit SQL access for Family form metadata and its scoped source reads."""


def add_response_web_grants(tables, columns):
    """Give web final responses, not source mutation or background fact claims."""
    for table in (
        "stewardship_snapshot_member",
        "stewardship_source_member",
        "stewardship_snapshot_contact",
        "stewardship_source_contact",
        "stewardship_snapshot_ministry",
        "stewardship_source_ministry",
        "stewardship_snapshot_roster",
        "stewardship_source_roster",
        "stewardship_snapshot_pledge",
        "stewardship_source_pledge",
        "stewardship_snapshot_contribution",
        "stewardship_source_contribution",
        "stewardship_submission",
    ):
        tables[table] = {"SELECT"}
    tables["stewardship_family_form_baseline"] = {"SELECT", "INSERT"}
    columns["stewardship_family_form_baseline"] = {
        "UPDATE": {"state", "ended_at", "version"}
    }
    # The source-pin trigger additionally restricts this login to form pins.
    tables["stewardship_source_pin"] = {"SELECT", "INSERT", "DELETE"}
    columns["stewardship_source_current"] = {"UPDATE": {"id"}}
    # Extend, never replace, the setup catalog's task/fence metadata reads.
    snapshot = columns.setdefault("stewardship_source_snapshot", {})
    snapshot.setdefault("SELECT", set()).update(
        {"id", "promoted_at", "state", "compacted_at", "generation", "cursor"}
    )
    snapshot.setdefault("UPDATE", set()).add("id")
    for table in (
        "stewardship_submission",
        "stewardship_submission_receipt",
        "stewardship_proposed_change",
        "stewardship_ministry_request",
        "stewardship_additional_information",
        "stewardship_fact_demand",
    ):
        tables[table] = {"SELECT", "INSERT"}
    columns["stewardship_proposed_change"] = {
        "UPDATE": {"execution", "superseded_by_id", "version"}
    }
    # The guard sets resolved_at itself; Staff never write roster evidence.
    columns["stewardship_ministry_request"] = {
        "UPDATE": {"state", "superseded_by_id", "version", "outcome", "assignee_id"}
    }
    tables["stewardship_ministry_revision"] = {"SELECT", "INSERT"}
    columns["stewardship_additional_information"] = {
        "UPDATE": {
            "disposition",
            "replacement_id",
            "version",
            "follow_up_needed",
            "followed_up_at",
        }
    }
    tables["stewardship_information_revision"] = {"SELECT", "INSERT"}
    columns["stewardship_family_campaign"]["UPDATE"].update(
        {"first_live_submission_id", "effective_submission_id"}
    )
    columns["stewardship_fact_demand"] = {
        "UPDATE": {
            "requested_source_id",
            "requested_source_generation",
            "requested_submission_watermark",
            "requested_timezone_configuration_id",
            "requested_through_date",
            "pending_revision",
            "pending_first_at",
            "pending_last_at",
            "pending_due_at",
            "version",
        }
    }
