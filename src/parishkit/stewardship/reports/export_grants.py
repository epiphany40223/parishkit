"""Explicit export-owner SQL grants; mail/backup consumers gain no report access."""


def add_export_grants(tables, columns, *, role):
    """Grant each role only its implemented request, rendering or download boundary."""
    if role not in {"web", "download", "worker", "scheduler"}:
        raise ValueError("Unknown export owner role.")
    for name in ("request", "attempt", "publication", "cancellation", "cleanup"):
        tables.setdefault("stewardship_export_" + name, set()).add("SELECT")
    if role in {"web", "download"}:
        for name in ("download_grant", "download_use"):
            tables.setdefault("stewardship_export_" + name, set()).add("SELECT")
    if role == "web":
        for name in ("request", "cancellation", "download_grant", "download_use"):
            tables["stewardship_export_" + name].add("INSERT")
        tables["stewardship_daily_fact_set"] = {"SELECT"}
        tables["stewardship_fact_pin"] = {"SELECT", "INSERT"}
        columns["stewardship_daily_fact_set"] = {"UPDATE": {"id"}}
    if role == "worker":
        for name in ("attempt", "publication", "cleanup"):
            tables["stewardship_export_" + name].add("INSERT")
        for name in ("daily_fact_set", "daily_fact", "fact_pin", "assignment_overlay"):
            tables.setdefault("stewardship_" + name, set()).add("SELECT")
    if role in {"worker", "scheduler"}:
        columns.setdefault("stewardship_portal_user", {}).setdefault(
            "SELECT", set()
        ).update({"id", "email", "hosted_domain", "disabled"})
    if role == "download":
        columns.setdefault("stewardship_campaign_credentials", {}).setdefault(
            "SELECT", set()
        ).update({"campaign_id", "go_live_gate"})
        columns.setdefault("stewardship_campaign_work_gate", {}).setdefault(
            "SELECT", set()
        ).update({"campaign_id", "state"})
