"""Least-privilege metadata production and ordinary fact materialization grants."""

from .fact_fields import REQUEST_UPDATE_FIELDS


def add_fact_grants(tables, columns, *, worker):
    """Schedulers cannot read answers/money or stage/publish calculated facts."""
    # stewardship_fact_disposable uses invoker rights. Scheduler selection needs
    # its fact/pointer/pin/demand and ownership metadata (some grants live in
    # other modules), but no calculated report values.
    for name in (
        "fact_demand",
        "daily_fact_set",
        "fact_build_receipt",
        "fact_pointer",
        "fact_pin",
    ):
        tables.setdefault("stewardship_" + name, set()).add("SELECT")
    for name in ("request", "result"):
        tables.setdefault("stewardship_fact_verification_" + name, set()).add("SELECT")
    tables["stewardship_fact_verification_" + ("result" if worker else "request")].add(
        "INSERT"
    )
    tables["stewardship_fact_demand"].add("INSERT")
    columns.setdefault("stewardship_fact_demand", {}).setdefault(
        "UPDATE", set()
    ).update(
        field + "_id"
        if field in {"requested_source", "requested_timezone_configuration"}
        else field
        for field in REQUEST_UPDATE_FIELDS
    )
    columns.setdefault("stewardship_submission", {}).setdefault("SELECT", set()).update(
        {"campaign_id", "mode", "campaign_sequence"}
    )
    if worker:
        tables["stewardship_fact_demand"].add("UPDATE")
        tables["stewardship_daily_fact_set"].update({"INSERT", "UPDATE"})
        tables["stewardship_fact_build_receipt"].add("INSERT")
        tables.setdefault("stewardship_daily_fact", set()).update({"SELECT", "INSERT"})
        tables["stewardship_fact_pointer"].update({"INSERT", "UPDATE"})
        columns["stewardship_submission"]["SELECT"].update(
            {"family_id", "submitted_at", "annual_pledge"}
        )
