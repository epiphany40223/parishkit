"""Imported past-campaign archives, kept as parish records (#1018, slice 2).

The campaign comparison report reads past campaigns from archive workbooks
(``archive.workbook``). An import keeps only aggregates and, when the
Administrator keeps Family rows, each Family's id and first campaign day for
the renewal figure (campaign-history spec, "What is stored"):

- ``CampaignArchive``: one row per import, with the file's identity
  (SHA-256 and size), its metadata, the recomputed totals and the pledging
  Families' quartiles, and its state: ``pending`` (Review's preview),
  ``active`` (Apply) or ``removed`` (a tombstone after Remove or Replace);
- ``CampaignArchiveDay``: the ``daily`` sheet, day by day;
- ``CampaignArchiveBand``: the pledge distribution, computed at import;
- ``CampaignArchiveFamily``: the renewal fields only.

They are parish records, not campaign records: no campaign purge touches
them. Rows are inserted once; SQL (``schema/migrations/0059_campaign_archives.sql``)
admits only the state changes pending to active and active to removed, and
deletes only of a pending archive (expired-preview housekeeping) and of the
child rows of a pending or removed one.
"""

from django.db import models

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

ARCHIVE_STATES = ("pending", "active", "removed")
# The id systems a kept Family row may name; an envelope-only Family has no
# id to match across years and is never kept.
STORED_ID_SYSTEMS = ("parishsoft_duid", "pds_fid")
# The upload limit of one archive workbook (campaign-history spec).
MAX_ARCHIVE_BYTES = 20 * 1024 * 1024
MONEY = {"max_digits": 18, "decimal_places": 2}


class CampaignArchive(MutableRecord):
    """One imported archive workbook: its metadata, totals and state.

    ``actor_id`` and ``created_at`` are who previewed it and when (Review);
    ``imported_by_id``/``imported_at`` who applied it, and
    ``removed_by_id``/``removed_at`` who removed or replaced it. ``notes``
    holds the file's ``note.*`` rows and the ``count.*`` rows the reader does
    not recompute, for the report's data notes; they never leave the Admin
    portal.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "checksum",
        "file_name",
        "file_size",
        "schema_version",
        "campaign_name",
        "campaign_year",
        "is_final",
        "as_of",
        "timezone",
        "start_date",
        "last_submission_date",
        "active_families",
        "active_families_provenance",
        "source_system",
        "dedup_rule",
        "family_id_systems",
        "family_rows_kept",
        "notes",
        "days",
        "families_responded",
        "submission_rows",
        "unattributed_rows",
        "ministry_rows",
        "pledging_families",
        "annual_pledged",
        "first_quartile",
        "median",
        "third_quartile",
        "band_set_version",
    )

    checksum = models.CharField(max_length=64)
    file_name = models.CharField(max_length=255, default="", blank=True)
    file_size = models.PositiveIntegerField()
    schema_version = models.CharField(max_length=16)
    campaign_name = models.CharField(max_length=200)
    campaign_year = models.PositiveSmallIntegerField()
    is_final = models.BooleanField()
    as_of = UTCDateTimeField()
    timezone = models.CharField(max_length=64)
    start_date = models.DateField()
    last_submission_date = models.DateField()
    active_families = models.PositiveIntegerField()
    active_families_provenance = models.TextField()
    source_system = models.TextField()
    dedup_rule = models.TextField()
    family_id_systems = models.JSONField()
    family_rows_kept = models.BooleanField()
    notes = models.JSONField()
    days = models.PositiveIntegerField()
    families_responded = models.PositiveIntegerField()
    submission_rows = models.PositiveIntegerField()
    unattributed_rows = models.PositiveIntegerField()
    ministry_rows = models.PositiveIntegerField()
    pledging_families = models.PositiveIntegerField()
    annual_pledged = models.DecimalField(**MONEY)
    first_quartile = models.DecimalField(null=True, **MONEY)
    median = models.DecimalField(null=True, **MONEY)
    third_quartile = models.DecimalField(null=True, **MONEY)
    band_set_version = models.PositiveSmallIntegerField()
    state = models.CharField(max_length=8, default="pending")
    imported_by_id = models.UUIDField(null=True)
    imported_at = UTCDateTimeField(null=True)
    removed_by_id = models.UUIDField(null=True)
    removed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_campaign_archive"
        constraints = [
            *MutableRecord.Meta.constraints,
            # One active import per file and per year; the loser of two
            # racing Applies is refused here.
            models.UniqueConstraint(
                fields=("checksum",),
                condition=models.Q(state="active"),
                name="campaign_archive_active_checksum",
            ),
            models.UniqueConstraint(
                fields=("campaign_year",),
                condition=models.Q(state="active"),
                name="campaign_archive_active_year",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=ARCHIVE_STATES),
                name="campaign_archive_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    checksum__regex=r"^[0-9a-f]{64}$",
                    campaign_year__gte=2000,
                    campaign_year__lte=2100,
                    file_size__gte=1,
                    file_size__lte=MAX_ARCHIVE_BYTES,
                    active_families__gte=1,
                    days__gte=1,
                    pledging_families__lte=models.F("families_responded"),
                    annual_pledged__gte=0,
                    band_set_version__gte=1,
                    last_submission_date__gte=models.F("start_date"),
                ),
                name="campaign_archive_values",
            ),
            # The three quartiles together, or none (no pledging Family).
            models.CheckConstraint(
                condition=models.Q(
                    first_quartile__isnull=True,
                    median__isnull=True,
                    third_quartile__isnull=True,
                )
                | models.Q(
                    first_quartile__isnull=False,
                    median__isnull=False,
                    third_quartile__isnull=False,
                    first_quartile__lte=models.F("median"),
                    median__lte=models.F("third_quartile"),
                ),
                name="campaign_archive_quartiles",
            ),
            # Who and when go with the state that records them.
            models.CheckConstraint(
                condition=models.Q(
                    state="pending",
                    imported_by_id__isnull=True,
                    imported_at__isnull=True,
                    removed_by_id__isnull=True,
                    removed_at__isnull=True,
                )
                | models.Q(
                    state="active",
                    imported_by_id__isnull=False,
                    imported_at__isnull=False,
                    removed_by_id__isnull=True,
                    removed_at__isnull=True,
                )
                | models.Q(
                    state="removed",
                    imported_by_id__isnull=False,
                    imported_at__isnull=False,
                    removed_by_id__isnull=False,
                    removed_at__isnull=False,
                ),
                name="campaign_archive_state_attribution",
            ),
        ]


class CampaignArchiveDay(ImmutableRecord):
    """One ``daily`` row of an archive; percentages are derived when read."""

    archive = models.ForeignKey(
        CampaignArchive, on_delete=models.PROTECT, related_name="day_rows"
    )
    day = models.PositiveIntegerField()
    local_date = models.DateField()
    first_submissions = models.PositiveIntegerField()
    cumulative_families = models.PositiveIntegerField()
    cumulative_pledging_families = models.PositiveIntegerField()
    cumulative_annual_pledged = models.DecimalField(**MONEY)

    class Meta:
        db_table = "stewardship_campaign_archive_day"
        constraints = [
            models.UniqueConstraint(
                fields=("archive", "day"), name="campaign_archive_day_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    day__gte=1,
                    cumulative_pledging_families__lte=models.F("cumulative_families"),
                    cumulative_annual_pledged__gte=0,
                ),
                name="campaign_archive_day_values",
            ),
        ]


class CampaignArchiveBand(ImmutableRecord):
    """One pledge band of an archive: its Families and their total pledged."""

    archive = models.ForeignKey(
        CampaignArchive, on_delete=models.PROTECT, related_name="band_rows"
    )
    band_set_version = models.PositiveSmallIntegerField()
    band = models.PositiveSmallIntegerField()
    families = models.PositiveIntegerField()
    total = models.DecimalField(**MONEY)

    class Meta:
        db_table = "stewardship_campaign_archive_band"
        constraints = [
            models.UniqueConstraint(
                fields=("archive", "band_set_version", "band"),
                name="campaign_archive_band_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(band_set_version__gte=1, band__gte=1, total__gte=0),
                name="campaign_archive_band_values",
            ),
        ]


class CampaignArchiveFamily(ImmutableRecord):
    """A responding Family kept for the renewal figure: id and first day only."""

    archive = models.ForeignKey(
        CampaignArchive, on_delete=models.PROTECT, related_name="family_rows"
    )
    family_id_system = models.CharField(max_length=16)
    family_id = models.PositiveBigIntegerField()
    first_campaign_day = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_campaign_archive_family"
        constraints = [
            models.UniqueConstraint(
                fields=("archive", "family_id_system", "family_id"),
                name="campaign_archive_family_unique",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    family_id_system__in=STORED_ID_SYSTEMS,
                    family_id__gte=1,
                    first_campaign_day__gte=1,
                ),
                name="campaign_archive_family_values",
            ),
        ]
