"""The setup wizard's quick parish data check (#2201).

Check parish data replaced the setup load: the isolated ParishSoft installer
asks ParishSoft, with the staged key, for the chosen organization's name and
its Family and Member totals, and records them here for the setup attempt's
own Administrator. A ready check bound to the staged key's current version,
fingerprint and organization is what Review and final confirmation require
(``setup_checks.current_check``); the full load runs only at Finish setup.
No Family or Member content is ever stored.
"""

from django.db import models

from parishkit.stewardship.storage import MutableRecord, UTCDateTimeField

# Each state a check can be in. ``queued`` and ``running`` wait for the
# installer; ``ready`` and ``failed`` are its answer; ``discarded`` is a
# check that never answered (a new key, or setup ended).
CHECK_STATES = ("queued", "running", "ready", "failed", "discarded")
# The states a check waits for its answer in: at most one per attempt.
WAITING_STATES = ("queued", "running")
# The stable error codes a failed check records, as the spec names them.
CHECK_ERRORS = (
    "parishsoft_key_refused",
    "parishsoft_organization_not_reached",
    "parishsoft_no_families",
    "parishsoft_check_unreadable",
    "parishsoft_unavailable",
)
# The largest total the check stores (a PostgreSQL integer).
MAX_TOTAL = 2**31 - 1


class SetupSourceCheck(MutableRecord):
    """One parish data check of one staged ParishSoft key and organization.

    ``credential``, ``credential_version``, ``fingerprint`` and
    ``organization_id`` bind it to exactly what it checked. A ready check
    holds ``organization_name`` (None when ParishSoft names nothing) and the
    two totals; a failed one only its ``error_code``. The SQL guard (frozen
    migration 0183) admits each move for its one owner: the web for the
    request and the discard, the ParishSoft installer for the claim and the
    answer.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "attempt_id",
        "credential_id",
        "credential_version",
        "fingerprint",
        "organization_id",
    )
    write_once_fields = ("worker_id", "started_at", "deadline_at", "finished_at")
    attempt = models.ForeignKey("SetupAttempt", on_delete=models.PROTECT)
    credential = models.ForeignKey("SetupSealedCredential", on_delete=models.PROTECT)
    credential_version = models.PositiveBigIntegerField()
    fingerprint = models.CharField(max_length=64)
    organization_id = models.PositiveBigIntegerField()
    state = models.CharField(max_length=16, default="queued")
    error_code = models.CharField(max_length=48, null=True)
    organization_name = models.CharField(max_length=200, null=True)
    family_count = models.PositiveBigIntegerField(null=True)
    member_count = models.PositiveBigIntegerField(null=True)
    worker_id = models.UUIDField(null=True)
    started_at = UTCDateTimeField(null=True)
    deadline_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_setup_source_check"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["attempt"],
                condition=models.Q(state__in=list(WAITING_STATES)),
                name="setup_check_one_waiting",
            ),
            models.CheckConstraint(
                condition=models.Q(state__in=list(CHECK_STATES)),
                name="setup_check_known_state",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    credential_version__gte=1,
                    fingerprint__regex=r"^[0-9a-f]{64}$",
                    organization_id__gte=1,
                    organization_id__lt=2**31,
                ),
                name="setup_check_binding_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(~models.Q(state="failed"), error_code__isnull=True)
                | models.Q(state="failed", error_code__in=list(CHECK_ERRORS)),
                name="setup_check_error_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="ready",
                    error_code__isnull=True,
                    family_count__gte=1,
                    family_count__lte=MAX_TOTAL,
                    member_count__gte=0,
                    member_count__lte=MAX_TOTAL,
                    finished_at__isnull=False,
                )
                | models.Q(
                    ~models.Q(state="ready"),
                    organization_name__isnull=True,
                    family_count__isnull=True,
                    member_count__isnull=True,
                ),
                name="setup_check_result_shape",
            ),
        ]
