"""Preparation grants do not confer dispatch, private-token reads or outbox edits."""


def add_family_mail_grants(tables, columns, *, worker):
    """Schedulers allocate opaque tickets; workers insert narrowly guarded mail."""
    tables.setdefault("stewardship_family_mail_preparation", set()).add("SELECT")
    if not worker:
        tables["stewardship_family_mail_preparation"].add("INSERT")
        tables.setdefault("stewardship_rehearsal_epoch", set()).add("INSERT")
        columns.setdefault("stewardship_campaign_credentials", {}).setdefault(
            "UPDATE", set()
        ).update({"rehearsal_epoch_id", "version", "actor_id", "correlation_id"})
        return
    for table in (
        "stewardship_rehearsal_code_mac",
        "stewardship_rehearsal_reservation",
    ):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    tables.setdefault("stewardship_rehearsal_credential", set()).add("INSERT")
    for table in (
        "stewardship_outbox_message",
        "stewardship_outbox_render",
        "stewardship_outbox_event",
    ):
        tables.setdefault(table, set()).update({"SELECT", "INSERT"})
    columns.setdefault("stewardship_rehearsal_credential", {}).setdefault(
        "SELECT", set()
    ).update(
        {
            "id",
            "epoch_id",
            "family_id",
            "code_ciphertext",
            "created_at",
            "updated_at",
            "actor_id",
            "correlation_id",
        }
    )
    columns.setdefault("stewardship_family_token", {}).setdefault(
        "SELECT", set()
    ).update({"id", "family_id", "campaign_id", "generation_id", "destroyed_at"})
