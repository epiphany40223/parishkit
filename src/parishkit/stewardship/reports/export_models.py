"""Append-only requester work, attempt ownership and publication receipts.

TaskRun owns execution state. Cancellation and publication are mutually exclusive
immutable outcomes; expiry removes bytes, never the request or its input pins.
An attempt UUID is recorded before filesystem work, including failed attempts.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord, UTCDateTimeField


class InformationExportSnapshot(ImmutableRecord):
    """Database-captured complete text/history, reusable after file expiration."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_information_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="information_export_correlation"
            ),
            models.Index(fields=("campaign",), name="information_export_campaign"),
            models.Index(fields=("source",), name="information_export_source"),
            models.Index(fields=("configuration",), name="information_export_config"),
        ]


class DirectoryExportSnapshot(ImmutableRecord):
    """Complete source/contact capture; stable Family references, never codes."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_directory_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="directory_export_correlation"
            ),
            models.Index(fields=("campaign",), name="directory_export_campaign"),
            models.Index(fields=("source",), name="directory_export_source"),
            models.Index(fields=("configuration",), name="directory_export_config"),
        ]


class MinistryExportSnapshot(ImmutableRecord):
    """Immutable complete Ministry result with its SQL-derived privacy scope."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    authorization_scope = models.JSONField()
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_ministry_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="ministry_export_correlation"
            ),
            models.Index(fields=("campaign",), name="ministry_export_campaign"),
            models.Index(fields=("source",), name="ministry_export_source"),
            models.Index(fields=("configuration",), name="ministry_export_config"),
        ]


class FinancialExportSnapshot(ImmutableRecord):
    """Immutable complete financial result, captured by SQL with its giving proof."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_financial_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="financial_export_correlation"
            ),
            models.Index(fields=("campaign",), name="financial_export_campaign"),
            models.Index(fields=("source",), name="financial_export_source"),
            models.Index(fields=("configuration",), name="financial_export_config"),
        ]


class FamilyTestNamesSnapshot(ImmutableRecord):
    """The names behind one chosen-Family test review, for its export only (#817).

    The Send to chosen Families page shows each DUID with its Family's name;
    ``pk-admin test families-preview --names`` keeps those names off the
    terminal by capturing them here, in the review's own transaction, for an
    export file. ``parameters`` holds the email revision and the DUIDs in the
    review's order; ``document`` holds one ``{"duid", "name"}`` row per DUID
    in that order. SQL checks that shape and who may capture it.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField()
    row_count = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_family_test_names_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="family_test_names_correlation"
            ),
            models.Index(fields=("campaign",), name="family_test_names_campaign"),
            models.Index(fields=("configuration",), name="family_test_names_config"),
        ]


class TimelineExportSnapshot(ImmutableRecord):
    """One Family's timeline as its page showed it, captured when requested.

    The page builds the timeline in Python (``timelines.read_timeline``),
    so the web captures that same read into ``document`` and SQL checks
    everything around it (migration 0063): an Administrator requester, an
    admitted campaign, the Family's campaign, the document's closed shape and
    ``row_count`` (its events). The Family code is never stored; the worker
    decrypts it into the file for a requester who may see codes.
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    family = models.ForeignKey(
        "stewardship_campaigns.FamilyCampaign",
        on_delete=models.PROTECT,
        db_index=False,
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_timeline_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="timeline_export_correlation"
            ),
            models.Index(fields=("campaign",), name="timeline_export_campaign"),
            models.Index(fields=("family",), name="timeline_export_family"),
            models.Index(fields=("configuration",), name="timeline_export_config"),
        ]


class ComparisonExportSnapshot(ImmutableRecord):
    """The Campaign comparison as its downloads may hold it, captured when requested.

    The page reads its figures in Python (``comparison_reads``), so the web
    stores the protected capture (``comparison_exports.capture``: aggregates
    only, the small-count protection applied, no note text) in
    ``document``, and SQL checks everything around it (migration 0071): an
    Administrator or Staff requester, an admitted campaign, the active
    configuration, the document's closed shape and ``row_count`` (its Every
    number rows).
    """

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion",
        on_delete=models.PROTECT,
        db_index=False,
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    parameters = models.JSONField()
    document = models.JSONField(default=dict)
    row_count = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "stewardship_comparison_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="comparison_export_correlation"
            ),
            models.Index(fields=("campaign",), name="comparison_export_campaign"),
            models.Index(fields=("configuration",), name="comparison_export_config"),
        ]


class ExportRequest(ImmutableRecord):
    """One canonical report request and its exact retained calculation generation."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    requester_id = models.UUIDField()
    request_key = models.UUIDField()
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fact_set = models.ForeignKey(
        "CampaignDailyFactSet", on_delete=models.PROTECT, null=True
    )
    information_snapshot = models.ForeignKey(
        InformationExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    directory_snapshot = models.ForeignKey(
        DirectoryExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    ministry_snapshot = models.ForeignKey(
        MinistryExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    financial_snapshot = models.ForeignKey(
        FinancialExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    family_test_names_snapshot = models.ForeignKey(
        FamilyTestNamesSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    timeline_snapshot = models.ForeignKey(
        TimelineExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    comparison_snapshot = models.ForeignKey(
        ComparisonExportSnapshot, on_delete=models.PROTECT, null=True, db_index=False
    )
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    report = models.CharField(max_length=32)
    format = models.CharField(max_length=4)
    browser_timezone = models.CharField(max_length=254)
    parameters = models.JSONField()
    authorization_scope = models.JSONField()

    class Meta:
        db_table = "stewardship_export_request"
        constraints = [
            models.UniqueConstraint(
                fields=("requester_id", "request_key"), name="export_request_replay"
            ),
            # A participation or archive export uses a fact generation until
            # it ends; its discard then clears the link (#1398, migration
            # 0109), and the request guard requires one on every new request.
            models.CheckConstraint(
                condition=models.Q(
                    report="participation",
                    information_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                )
                | models.Q(
                    report="additional_information",
                    fact_set__isnull=True,
                    information_snapshot__isnull=False,
                    ministry_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                | models.Q(
                    report__in=("family_directory", "postal_outreach"),
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    directory_snapshot__isnull=False,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                | models.Q(
                    report="ministry",
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=False,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                | models.Q(
                    report="financial",
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    financial_snapshot__isnull=False,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                # The names behind a chosen-Family test review (#817), from
                # its own capture; CSV, XLSX or PDF since 0049 (#966).
                | models.Q(
                    report="family_test_names",
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=False,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                # One Family's timeline, from its own capture (migration 0063).
                | models.Q(
                    report="family_timeline",
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=False,
                    comparison_snapshot__isnull=True,
                    format__in=("csv", "xlsx", "pdf"),
                )
                # The Campaign comparison, from its own capture (migration
                # 0071, #1018).
                | models.Q(
                    report="campaign_comparison",
                    fact_set__isnull=True,
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=False,
                    format__in=("csv", "xlsx", "pdf"),
                )
                # The campaign archive workbook: one XLSX of a ready
                # historical fact set, read by the worker under the
                # request's pins, with no capture (migration 0084, #1018).
                | models.Q(
                    report="campaign_archive",
                    information_snapshot__isnull=True,
                    directory_snapshot__isnull=True,
                    ministry_snapshot__isnull=True,
                    financial_snapshot__isnull=True,
                    family_test_names_snapshot__isnull=True,
                    timeline_snapshot__isnull=True,
                    comparison_snapshot__isnull=True,
                    format="xlsx",
                ),
                name="export_report_known",
            ),
            models.CheckConstraint(
                condition=models.Q(format__in=("csv", "png", "pdf", "xlsx")),
                name="export_format_known",
            ),
        ]
        indexes = [
            models.Index(
                fields=("requester_id", "created_at"), name="export_requester_history"
            ),
            models.Index(
                fields=("information_snapshot",), name="export_information_snapshot"
            ),
            models.Index(
                fields=("directory_snapshot",), name="export_directory_snapshot"
            ),
            models.Index(
                fields=("ministry_snapshot",), name="export_ministry_snapshot"
            ),
            models.Index(
                fields=("financial_snapshot",), name="export_financial_snapshot"
            ),
            models.Index(
                fields=("family_test_names_snapshot",),
                name="export_family_test_names",
            ),
            models.Index(
                fields=("timeline_snapshot",), name="export_timeline_snapshot"
            ),
            models.Index(
                fields=("comparison_snapshot",), name="export_comparison_snapshot"
            ),
        ]


class ParticipationExportSnapshot(ImmutableRecord):
    """The Response dashboard's Summary, frozen when its export is requested.

    The page reads the statistics and the funnel in Python, so the web
    captures that same read (``participation_summary.capture``) into
    ``document`` in the request's own transaction, and SQL checks
    everything around it (frozen migration 0146, #1557): one capture per
    participation request, by its requester, from the web, before any
    attempt, and the document's closed shape. Unlike the other captures it
    names its request (the request is made first), and a deferred check
    requires one on every participation request but a latest-data handoff.
    """

    request = models.OneToOneField(
        ExportRequest, on_delete=models.PROTECT, related_name="summary_snapshot"
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT, db_index=False
    )
    actor_id = models.UUIDField(editable=False)
    correlation_id = models.UUIDField(editable=False)
    document = models.JSONField()

    class Meta:
        db_table = "stewardship_participation_export_snapshot"
        indexes = [
            models.Index(
                fields=("correlation_id",), name="participation_export_correl"
            ),
            models.Index(fields=("campaign",), name="participation_export_campaign"),
        ]


class ExportAttempt(ImmutableRecord):
    """An artifact UUID allocated before rendering, bound to an exact worker claim."""

    request = models.ForeignKey(ExportRequest, on_delete=models.PROTECT)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    claim_event = models.ForeignKey(
        "stewardship_jobs.TaskRunEvent", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_export_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=("request", "run", "fence"), name="export_attempt_claim"
            ),
            models.CheckConstraint(
                condition=models.Q(fence__gt=0), name="export_attempt_positive_fence"
            ),
        ]


class ExportPublication(ImmutableRecord):
    """A successful artifact; metadata/pinned calculations outlive file expiration."""

    request = models.OneToOneField(ExportRequest, on_delete=models.PROTECT)
    attempt = models.OneToOneField(ExportAttempt, on_delete=models.PROTECT)
    size = models.PositiveBigIntegerField()
    sha256 = models.CharField(max_length=64)
    row_count = models.PositiveIntegerField()
    expires_at = UTCDateTimeField()

    class Meta:
        db_table = "stewardship_export_publication"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(size__gt=0, size__lte=536870912),
                name="export_publication_size",
            ),
            models.CheckConstraint(
                condition=models.Q(sha256__regex=r"^[0-9a-f]{64}$"),
                name="export_publication_digest",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="export_publication_expiry",
            ),
        ]
        indexes = [models.Index(fields=("expires_at",), name="export_expiry")]


class ExportCancellation(ImmutableRecord):
    """A safe-point cancellation request, never an instruction to kill a process."""

    request = models.OneToOneField(ExportRequest, on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_export_cancellation"


class ExportDownloadGrant(ImmutableRecord):
    """Opaque short-lived grant tied to a requester and the exact published bytes."""

    # Security metadata, not campaign-owned work: issuing a grant for an already
    # complete file may continue during purge preparation without changing its
    # campaign inventory. Fresh guarded lookup still rejects a purged target.
    publication_id = models.UUIDField()
    requester_id = models.UUIDField()
    expires_at = UTCDateTimeField()

    class Meta:
        db_table = "stewardship_export_download_grant"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("created_at")),
                name="export_download_expiry",
            ),
        ]


class ExportDownloadUse(ImmutableRecord):
    """One-use consumption committed before streaming; retries require a new grant."""

    grant = models.OneToOneField(ExportDownloadGrant, on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_export_download_use"


class ExportArtifactCleanup(ImmutableRecord):
    """An expired/unpublished attempt's exact file removal, not history deletion."""

    attempt = models.OneToOneField(ExportAttempt, on_delete=models.PROTECT)
    task = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()

    class Meta:
        db_table = "stewardship_export_cleanup"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(fence__gt=0), name="export_cleanup_positive_fence"
            ),
        ]


class ExportDiscard(ImmutableRecord):
    """An ended export's saved data, discarded once (#1398, frozen 0109).

    Exports are point-in-time (operations spec, "Point-in-time exports"):
    when an export ends, its capture's document is cleared and its fact and
    source pins are released. This row is the durable record that it was
    done, naming one report export or one latest-data export, how many
    captures were cleared and pins released, and whether the deploy-time
    sweep did it. Only the SQL discard functions write it.
    """

    request = models.OneToOneField(
        ExportRequest, null=True, blank=True, on_delete=models.PROTECT
    )
    exact_request = models.OneToOneField(
        "stewardship_reports.ExactExportRequest",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    documents = models.PositiveSmallIntegerField()
    pins = models.PositiveIntegerField()
    by_deploy = models.BooleanField()

    class Meta:
        db_table = "stewardship_export_discard"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(request__isnull=False, exact_request__isnull=True)
                | models.Q(request__isnull=True, exact_request__isnull=False),
                name="export_discard_one_request",
            ),
            models.CheckConstraint(
                condition=models.Q(documents__lte=1), name="export_discard_documents"
            ),
        ]
