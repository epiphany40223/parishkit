"""The setup wizard's ParishSoft organization lookup (#1554).

A staged ParishSoft key is sealed with no organization; the isolated
ParishSoft installer then asks ParishSoft which organizations the key
reaches, and whether each holds Families, and records the answer here for
the setup attempt's own Administrator to choose from. The other
organizations' IDs and names live only in ``organizations`` and are
scrubbed as soon as one is chosen, the key is discarded or the attempt
ends; the chosen ID and name stay for the wizard's own pages.
"""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

# Each state a lookup can be in. ``queued`` and ``running`` wait for the
# installer; ``ready`` and ``failed`` wait for the Administrator; ``chosen``
# and ``discarded`` are over (and scrubbed).
LOOKUP_STATES = ("queued", "running", "ready", "failed", "chosen", "discarded")
# The states a lookup is still open in: at most one per attempt.
OPEN_STATES = ("queued", "running", "ready", "failed")
# The stable error codes a failed lookup records, as the spec names them.
LOOKUP_ERRORS = (
    "parishsoft_key_refused",
    "parishsoft_no_organizations",
    "parishsoft_no_family_organizations",
    "parishsoft_organizations_unreadable",
    "parishsoft_unavailable",
)


class SetupParishSoftLookup(MutableRecord):
    """One organization lookup for one staged ParishSoft key version.

    ``organizations`` is the bounded list the installer read, each
    ``{"id": int, "name": str | None, "families": bool | None}`` (None:
    ParishSoft did not say), sorted as the page lists them; it is set only
    while ``ready`` and scrubbed (None) once the lookup is over. A failed
    lookup keeps only its ``error_code``. ``chosen_id`` and ``chosen_name``
    are the Administrator's choice. The SQL guard (frozen migration 0174)
    admits each move for its one owner: the web for the request, choice and
    discard, the ParishSoft installer for the answer.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "attempt_id",
        "credential_id",
        "credential_version",
        "fingerprint",
    )
    write_once_fields = (
        "worker_id",
        "started_at",
        "deadline_at",
        "finished_at",
        "scrubbed_at",
    )
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    credential = models.ForeignKey("SetupSealedCredential", on_delete=models.PROTECT)
    credential_version = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    state = models.CharField(max_length=16, default="queued")
    error_code = models.CharField(max_length=48, null=True)
    organizations = models.JSONField(null=True)
    chosen_id = models.PositiveBigIntegerField(null=True)
    chosen_name = models.CharField(max_length=200, null=True)
    worker_id = models.UUIDField(null=True)
    started_at = UTCDateTimeField(null=True)
    deadline_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)
    scrubbed_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_parishsoft_lookup"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["attempt"],
                condition=models.Q(state__in=list(OPEN_STATES)),
                name="setup_lookup_one_open",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=list(LOOKUP_STATES)),
                name="setup_lookup_known_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credential_version__gte=1, fingerprint__regex=r"^[0-9a-f]{64}$"
                ),
                name="setup_lookup_binding_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(error_code__isnull=True)
                | models.Q(state="failed", error_code__in=list(LOOKUP_ERRORS)),
                name="setup_lookup_error_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(organizations__isnull=True)
                | models.Q(state="ready"),
                name="setup_lookup_list_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="chosen", chosen_id__gte=1, chosen_id__lt=2**31
                )
                | models.Q(chosen_id__isnull=True, chosen_name__isnull=True),
                name="setup_lookup_choice_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state__in=["chosen", "discarded"], scrubbed_at__isnull=False
                )
                | models.Q(state__in=list(OPEN_STATES), scrubbed_at__isnull=True),
                name="setup_lookup_scrub_shape",
            ),
        ]
