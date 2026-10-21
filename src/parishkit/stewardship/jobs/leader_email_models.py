"""Ministry leader emails: one Administrator's request, its cohort and recipients.

An Administrator's Email Ministry leaders Apply (admin-portal specification,
"Email Ministry leaders", #1592) records one ``LeaderEmailRequest``. Its
database guard admits it only from the web login for a current
Administrator, and fills in each chosen leader's Ministries and open request
counts itself (``stewardship_leader_email_facts_v1``), so no form supplies
them. The scheduler then queues the request's preparation Task; the worker
freezes its addresses into a ``LeaderEmailCohort`` and writes one outbox
message per leader with its ``LeaderEmailRecipient``, which keeps the
compiled subject and bodies every attempt sends (background-processing
specification, "Ministry leader emails"). All three are append-only, and
none holds a parishioner's name or contact.
"""

from django.db import models

from parishkit.stewardship.storage import ImmutableRecord

# One Apply emails at most this many leaders (the request guard's bound too).
REQUEST_LIMIT = 100


class LeaderEmailRequest(ImmutableRecord):
    """One Administrator's Apply: who is emailed, in which mode, and the facts.

    ``addresses`` are the chosen leaders' sign-in addresses, distinct and
    lowercased. ``recipients`` is the guard's answer for them: one
    ``{"email", "ministries": [{"duid", "name", "join", "leave"}]}`` per
    address, in address order. ``testing_recipient`` is the Testing
    recipient every email goes to instead, set exactly in Testing.
    """

    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    campaign = models.ForeignKey(
        "stewardship_campaigns.Campaign", on_delete=models.PROTECT
    )
    mode = models.CharField(max_length=16)
    testing_recipient = models.CharField(max_length=254, null=True)
    addresses = models.JSONField()
    recipients = models.JSONField()
    recipient_count = models.PositiveIntegerField()

    class Meta:
        db_table = "stewardship_leader_email_request"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode="production", testing_recipient=None)
                | models.Q(mode="testing", testing_recipient__isnull=False),
                name="leader_email_request_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    recipient_count__gte=1, recipient_count__lte=REQUEST_LIMIT
                ),
                name="leader_email_request_count",
            ),
        ]


class LeaderEmailCohort(ImmutableRecord):
    """The request's addresses, frozen once by its fenced preparation Task."""

    request = models.OneToOneField(LeaderEmailRequest, on_delete=models.PROTECT)
    configuration = models.ForeignKey(
        "stewardship_accounts.AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    parish = models.ForeignKey("stewardship_accounts.Parish", on_delete=models.PROTECT)
    mode = models.CharField(max_length=16)
    testing_recipient = models.CharField(max_length=254, null=True)
    addresses = models.JSONField()
    recipient_count = models.PositiveIntegerField()
    run = models.ForeignKey("stewardship_jobs.TaskRun", on_delete=models.PROTECT)
    fence = models.PositiveBigIntegerField()
    worker_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_leader_email_cohort"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mode__in=("testing", "production")),
                name="leader_email_cohort_mode",
            ),
            models.CheckConstraint(
                condition=models.Q(recipient_count__gte=1, fence__gte=1),
                name="leader_email_cohort_counts",
            ),
        ]


class LeaderEmailRecipient(ImmutableRecord):
    """One leader's email: its outbox message and the content it sends.

    The compiled subject and bodies are kept here so that every attempt,
    retries included, sends exactly what preparation compiled; mail
    dispatch's SQL admission compares each render with them.
    """

    cohort = models.ForeignKey(LeaderEmailCohort, on_delete=models.PROTECT)
    address = models.CharField(max_length=254)
    outbox = models.OneToOneField(
        "stewardship_jobs.OutboxMessage", on_delete=models.PROTECT
    )
    subject = models.CharField(max_length=254)
    html = models.TextField()
    text = models.TextField()

    class Meta:
        db_table = "stewardship_leader_email_recipient"
        constraints = [
            models.UniqueConstraint(
                fields=["cohort", "address"], name="leader_email_recipient_address"
            ),
        ]
