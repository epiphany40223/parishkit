"""Durable per-Family engagement for response reporting (#477).

Family session rows are deleted 60 minutes after the last activity, and their
presence column holds only the current step, so a response funnel built on
them undercounts after a few hours and cannot be reproduced later. This record
keeps one row per Family and mode with the first instants that matter to the
funnel and the furthest form step reached. The sign-in, form-issuance and
presence-heartbeat paths maintain it with a monotonic upsert; SQL guards keep
every ``first_*`` instant from moving later and the furthest step from moving
back. It holds no answers, names or credentials.
"""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

from .credential_models import PRESENCE_SECTIONS, FamilyCampaign


class FamilyEngagement(MutableRecord):
    """First link, form and progress instants plus the furthest step, per mode."""

    immutable_fields = MutableRecord.immutable_fields + (
        "family_id",
        "mode",
        "rehearsal_epoch_id",
    )
    family = models.ForeignKey(FamilyCampaign, on_delete=models.PROTECT)
    # "live" or "test", as responses and form baselines spell their mode. A
    # Testing row names its rehearsal epoch so Production-transition cleanup
    # can select it exactly; a live row has none.
    mode = models.CharField(max_length=4)
    rehearsal_epoch_id = models.UUIDField(null=True)
    first_link_at = UTCDateTimeField(null=True)
    first_form_at = UTCDateTimeField(null=True)
    first_progress_at = UTCDateTimeField(null=True)
    furthest_section = models.CharField(max_length=24, default="", blank=True)
    furthest_at = UTCDateTimeField(null=True)
    last_seen_at = UTCDateTimeField()

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_family_engagement"
        indexes = [
            models.Index(
                fields=["mode", "first_link_at"], name="family_engagement_link"
            )
        ]
        constraints = MutableRecord.Meta.constraints + [
            # NULLS NOT DISTINCT: the live row (no epoch) is as unique as a
            # Testing row, so the upsert's ON CONFLICT finds it.
            models.UniqueConstraint(
                fields=["family", "mode", "rehearsal_epoch_id"],
                nulls_distinct=False,
                name="family_engagement_identity",
            ),
            models.CheckConstraint(
                condition=models.Q(mode="live", rehearsal_epoch_id__isnull=True)
                | models.Q(mode="test", rehearsal_epoch_id__isnull=False),
                name="family_engagement_mode_epoch",
            ),
            models.CheckConstraint(
                condition=models.Q(furthest_section="", furthest_at__isnull=True)
                | models.Q(
                    furthest_section__in=PRESENCE_SECTIONS, furthest_at__isnull=False
                ),
                name="family_engagement_furthest_shape",
            ),
            # Progress means a step past the first one; the first step alone
            # is "form opened", which first_form_at already records.
            models.CheckConstraint(
                condition=models.Q(
                    first_progress_at__isnull=True,
                    furthest_section__in=["", PRESENCE_SECTIONS[0]],
                )
                | models.Q(first_progress_at__isnull=False)
                & ~models.Q(furthest_section__in=["", PRESENCE_SECTIONS[0]]),
                name="family_engagement_progress_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(first_link_at__isnull=False)
                | models.Q(first_form_at__isnull=False)
                | models.Q(furthest_at__isnull=False),
                name="family_engagement_evidence",
            ),
            # last_seen_at is the latest instant the row knows about.
            models.CheckConstraint(
                condition=(
                    models.Q(first_link_at__isnull=True)
                    | models.Q(first_link_at__lte=models.F("last_seen_at"))
                )
                & (
                    models.Q(first_form_at__isnull=True)
                    | models.Q(first_form_at__lte=models.F("last_seen_at"))
                )
                & (
                    models.Q(first_progress_at__isnull=True)
                    | models.Q(first_progress_at__lte=models.F("last_seen_at"))
                )
                & (
                    models.Q(furthest_at__isnull=True)
                    | models.Q(furthest_at__lte=models.F("last_seen_at"))
                ),
                name="family_engagement_seen_last",
            ),
        ]
