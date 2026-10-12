"""Daily digest permissions: scheduler metadata is not access to private reports."""


def add_digest_grants(tables, columns, *, worker):
    """Share opaque ownership; only the general worker can capture report inputs."""
    tables.setdefault("stewardship_daily_digest_preparation", set()).add("SELECT")
    tables.setdefault("stewardship_recovery_replacement", set()).add("SELECT")
    tables.setdefault("stewardship_daily_digest_completion_ready", set()).add("SELECT")
    if worker:
        # The digest's response funnel (#477) reads the durable engagement
        # record's first instants, as the Response dashboard does: only the
        # columns the funnel needs, read only, never written.
        columns.setdefault("stewardship_family_engagement", {}).setdefault(
            "SELECT", set()
        ).update(
            {
                "family_id",
                "mode",
                "rehearsal_epoch_id",
                "first_link_at",
                "first_form_at",
                "first_progress_at",
            }
        )
        # The Compared with past campaigns section (#1018 slice 7) reads
        # the imported years' rows and bands, read only. Kept archive Family
        # rows stay unreadable: their renewal counts come from the definer
        # functions (runtime_grants.runtime_functions).
        from .archive_grants import ARCHIVE_TABLE

        for table in (
            ARCHIVE_TABLE,
            "stewardship_campaign_archive_day",
            "stewardship_campaign_archive_band",
        ):
            tables.setdefault(table, set()).add("SELECT")
        tables["stewardship_daily_digest_preparation"].add("UPDATE")
        for name in ("snapshot", "ready", "recipient"):
            tables.setdefault("stewardship_daily_digest_" + name, set()).update(
                {"SELECT", "INSERT"}
            )
    else:
        tables["stewardship_daily_digest_preparation"].add("INSERT")
        columns.setdefault("stewardship_daily_digest_snapshot", {}).setdefault(
            "SELECT", set()
        ).update(
            {
                "id",
                "campaign_id",
                "population_scope",
                "source_id",
                "submission_watermark",
                "timezone_configuration_id",
                "through_date",
            }
        )


def add_digest_dispatch_grants(tables, columns, *, private):
    """MAIL reads compiled content; scheduler sees only opaque relationship IDs."""
    tables.setdefault("stewardship_daily_digest_preparation", set()).add("SELECT")
    tables.setdefault("stewardship_daily_digest_completion_ready", set()).add("SELECT")
    columns.setdefault("stewardship_daily_digest_snapshot", {}).setdefault(
        "SELECT", set()
    ).update({"id", "preparation_id"})
    if private:
        for table in (
            "stewardship_daily_digest_ready",
            "stewardship_daily_digest_recipient",
        ):
            tables.setdefault(table, set()).add("SELECT")
        columns.setdefault("stewardship_address_rule", {}).setdefault(
            "SELECT", set()
        ).update({"email", "roles", "configuration_id"})
    else:
        for table, fields in {
            "stewardship_daily_digest_ready": {"id", "snapshot_id"},
            "stewardship_daily_digest_recipient": {"id", "ready_id", "outbox_id"},
        }.items():
            columns.setdefault(table, {}).setdefault("SELECT", set()).update(fields)


def add_digest_web_grants(tables, columns):
    """Read retry ownership and configured Admin routing, never compiled content."""
    add_digest_dispatch_grants(tables, columns, private=False)
    columns["stewardship_daily_digest_recipient"]["SELECT"].add("address")
    # Delivery observability above may read per-message Admin addresses. The
    # report reader gets retained inputs, not compiled mail prose or the ready
    # row's recipient snapshot. Its view rechecks Staff/Admin authority.
    tables.setdefault("stewardship_daily_digest_snapshot", set()).add("SELECT")
    # The saved page also shows the retained Compared with past campaigns
    # section (#1018 slice 7): protected aggregates, never mail prose.
    columns["stewardship_daily_digest_ready"]["SELECT"].update(
        {"fact_set_id", "chart", "comparison"}
    )


def add_digest_download_grants(columns):
    """The bounded download login can stream only the already-compiled chart."""
    for table, fields in {
        "stewardship_daily_digest_snapshot": {"id", "campaign_id", "configuration_id"},
        "stewardship_daily_digest_ready": {"id", "snapshot_id", "chart"},
    }.items():
        columns.setdefault(table, {}).setdefault("SELECT", set()).update(fields)
