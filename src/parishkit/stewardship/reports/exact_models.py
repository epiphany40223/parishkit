"""Frozen export intent before its exact calculation generation exists.

Requests and their input protection commit before execution. Resolution records
the atomic handoff to the existing export owner; neither retries nor later source
promotions can turn that handoff into a new report request.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord


class ExactExportRequest(ImmutableRecord):
    """One requester-owned immutable input tuple and compiled rendering intent."""

    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    requester_id = models.UUIDField()
    request_key = models.UUIDField()
    task = models.OneToOneField("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    population_scope = models.CharField(max_length=12)
    source = models.ForeignKey(
        "stewardship_source.SourceSnapshot", on_delete=models.PROTECT
    )
    submission_watermark = models.PositiveBigIntegerField()
    timezone_configuration = models.ForeignKey(
        "stewardship_campaigns.CampaignConfiguration", on_delete=models.PROTECT
    )
    through_date = models.DateField()
    format = models.CharField(max_length=4)
    browser_timezone = models.CharField(max_length=254)

    class Meta:
        db_table = "stewardship_exact_export_request"
        constraints = [
            models.UniqueConstraint(
                fields=("requester_id", "request_key"), name="exact_export_replay"
            ),
            models.CheckConstraint(
                condition=models.Q(population_scope__in=("historical", "current")),
                name="exact_export_scope",
            ),
            models.CheckConstraint(
                condition=models.Q(format__in=("csv", "png", "pdf", "xlsx")),
                name="exact_export_format",
            ),
        ]


class ExactExportCancellation(ImmutableRecord):
    """Cancellation before handoff; resolved requests use export cancellation."""

    request = models.OneToOneField(ExactExportRequest, on_delete=models.PROTECT)

    class Meta:
        db_table = "stewardship_exact_export_cancel"


class ExactExportResolution(ImmutableRecord):
    """A live fenced exact worker handed one ready generation to the renderer."""

    request = models.OneToOneField(ExactExportRequest, on_delete=models.PROTECT)
    export = models.OneToOneField("ExportRequest", on_delete=models.PROTECT)
    fact_set = models.ForeignKey("CampaignDailyFactSet", on_delete=models.PROTECT)
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_exact_export_resolution"
