"""What each online process last reported about itself (ADM-13, #530).

The System health page reads these rows to show which services run, which
version each one runs, whether debug logging is in effect and what each mail
consumer is doing; that state otherwise lives only inside each container
(heartbeat files and the mail circuit). The rows are for display only: no
sending, refresh or backup decision reads them, so a lost write can only make
the page out of date.

The guard in ``schema/migrations/0007_system_health_records.sql`` lets each
service's database login write only its own service's rows, sets every time
from the database clock and lets only the worker's housekeeping delete a row,
once it has not been reported for a day. Nothing here names a host, an
address, a message, a recipient or a credential.
"""

from django.db import models

from parishkit.stewardship.storage import UTCDateTimeField

# The online services that report, by their deployment service role. The SQL
# guard maps each to its own login (credential installers by target).
SERVICES = (
    "web",
    "worker",
    "scheduler",
    "mail-dispatch",
    "config-installer",
    "credential-installer",
)
# A container's main process (and each web worker) is "main"; the worker's
# source-queue sibling is "source" and mail dispatch's second consumer "mail".
PROCESSES = ("main", "source", "mail")
# A mail consumer's Family mail sender, in the words the page will show:
# running (sending, or ready with nothing due), paused after an outage, held
# at Gmail's sending limit, waiting for the deployment's daily limit, halted.
SENDER_STATES = ("running", "outage_paused", "gmail_held", "daily_limit", "halted")
# A sender state that ends by itself at a known time.
TIMED_SENDER_STATES = ("outage_paused", "gmail_held")


class ServiceStatus(models.Model):
    """One online process's latest report: identity, version and states.

    ``id`` is made by the process when it starts, so a restart is a new row;
    ``started_at`` and ``reported_at`` come from the database clock (the
    guard sets them), so a host clock never makes a process look alive.
    ``sender_*`` is set only for a mail-dispatch process; ``sender_since`` is
    when the database first saw the current state.
    """

    id = models.UUIDField(primary_key=True, editable=False)
    service = models.CharField(max_length=24)
    process = models.CharField(max_length=8)
    target = models.CharField(max_length=32, null=True, blank=True)
    started_at = UTCDateTimeField()
    reported_at = UTCDateTimeField()
    application_version = models.CharField(max_length=40)
    debug_logging = models.BooleanField()
    sender_state = models.CharField(max_length=16, null=True, blank=True)
    sender_since = UTCDateTimeField(null=True, blank=True)
    sender_until = UTCDateTimeField(null=True, blank=True)

    class Meta:
        db_table = "stewardship_service_status"
        indexes = [
            models.Index(fields=["reported_at"], name="service_status_reported"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(service__in=SERVICES)
                & models.Q(process__in=PROCESSES),
                name="service_status_names",
            ),
            # Only the worker has a source sibling and only mail dispatch a
            # second mail consumer; only a credential installer has a target.
            models.CheckConstraint(
                condition=(
                    models.Q(process="main")
                    | models.Q(process="source", service="worker")
                    | models.Q(process="mail", service="mail-dispatch")
                )
                & (
                    models.Q(service="credential-installer", target__isnull=False)
                    | (
                        ~models.Q(service="credential-installer")
                        & models.Q(target__isnull=True)
                    )
                ),
                name="service_status_process",
            ),
            models.CheckConstraint(
                condition=models.Q(target__isnull=True)
                | models.Q(target__regex=r"^[a-z][a-z0-9_]{0,31}$"),
                name="service_status_target",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    application_version__regex=r"^[0-9A-Za-z.+-]{1,40}$"
                ),
                name="service_status_version",
            ),
            models.CheckConstraint(
                condition=models.Q(started_at__lte=models.F("reported_at")),
                name="service_status_times",
            ),
            # A mail-dispatch process always reports its sender state with
            # when it began; no other service has one. An end time is known
            # only for a state that lifts by itself.
            models.CheckConstraint(
                condition=(
                    models.Q(
                        service="mail-dispatch",
                        sender_state__in=SENDER_STATES,
                        sender_since__isnull=False,
                    )
                    | (
                        ~models.Q(service="mail-dispatch")
                        & models.Q(sender_state__isnull=True, sender_since__isnull=True)
                    )
                )
                & (
                    models.Q(sender_until__isnull=True)
                    | models.Q(sender_state__in=TIMED_SENDER_STATES)
                ),
                name="service_status_sender",
            ),
        ]
