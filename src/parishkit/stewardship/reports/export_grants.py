"""Explicit export-owner SQL grants; mail/backup consumers gain no report access."""


def add_export_grants(tables, columns, *, role):
    """Grant each role only its implemented request, rendering or download boundary."""
    if role not in {"web", "download", "worker", "scheduler"}:
        raise ValueError("Unknown export owner role.")
    for name in ("request", "attempt", "publication", "cancellation", "cleanup"):
        tables.setdefault("stewardship_export_" + name, set()).add("SELECT")
    # Request guards and publication metadata read the retained input header;
    # only the render owner and web report owner can read captured private rows.
    for kind in ("information", "directory", "ministry"):
        snapshot = f"stewardship_{kind}_export_snapshot"
        if role in {"web", "worker"}:
            tables.setdefault(snapshot, set()).add("SELECT")
        else:
            columns.setdefault(snapshot, {}).setdefault("SELECT", set()).update(
                {"id", "campaign_id", "actor_id", "row_count"}
            )
        if role == "web":
            tables[snapshot].add("INSERT")
    if role == "web":
        # Staff directories expose only the selected source's known address.
        # No address mutation, source pin, token or private-key grant is added.
        columns.setdefault("stewardship_snapshot_address", {}).setdefault(
            "SELECT", set()
        ).update({"snapshot_id", "payload_id", "source_key"})
        columns.setdefault("stewardship_source_address", {}).setdefault(
            "SELECT", set()
        ).update({"id", "canonical"})
    if role != "download":
        for name in ("request", "cancel", "resolution"):
            tables.setdefault("stewardship_exact_export_" + name, set()).add("SELECT")
    if role in {"worker", "scheduler", "web"}:
        columns.setdefault("stewardship_portal_user", {}).setdefault(
            "SELECT", set()
        ).update({"id", "email", "hosted_domain", "disabled"})
    if role == "web":
        for name in ("request", "cancel"):
            tables["stewardship_exact_export_" + name].add("INSERT")
    if role == "worker":
        tables["stewardship_exact_export_resolution"].add("INSERT")
        tables["stewardship_export_request"].add("INSERT")
        tables.setdefault("stewardship_fact_pin", set()).add("INSERT")
    if role in {"web", "download"}:
        for name in ("download_grant", "download_use"):
            tables.setdefault("stewardship_export_" + name, set()).add("SELECT")
    if role == "web":
        for name in ("request", "cancellation", "download_grant", "download_use"):
            tables["stewardship_export_" + name].add("INSERT")
        # Statistics verify complete snapshot counts and scope permanent address
        # refusals by organization. No source mutation or raw validation detail.
        columns.setdefault("stewardship_source_snapshot", {}).setdefault(
            "SELECT", set()
        ).update({"counts", "organization_id"})
        tables.setdefault("stewardship_daily_fact_set", set()).add("SELECT")
        for name in ("daily_fact", "fact_pointer"):
            tables.setdefault("stewardship_" + name, set()).add("SELECT")
        columns.setdefault("stewardship_submission", {}).setdefault(
            "SELECT", set()
        ).update({"campaign_id", "mode", "campaign_sequence"})
        tables.setdefault("stewardship_fact_pin", set()).update({"SELECT", "INSERT"})
        columns.setdefault("stewardship_daily_fact_set", {}).setdefault(
            "UPDATE", set()
        ).add("id")
    if role == "worker":
        for name in ("attempt", "publication", "cleanup"):
            tables["stewardship_export_" + name].add("INSERT")
        for name in ("daily_fact_set", "daily_fact", "fact_pin", "assignment_overlay"):
            tables.setdefault("stewardship_" + name, set()).add("SELECT")
    if role == "download":
        columns.setdefault("stewardship_campaign_credentials", {}).setdefault(
            "SELECT", set()
        ).update({"campaign_id", "go_live_gate"})
        columns.setdefault("stewardship_campaign_work_gate", {}).setdefault(
            "SELECT", set()
        ).update({"campaign_id", "state"})
