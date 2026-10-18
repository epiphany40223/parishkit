"""Clear a halted mail sender: halts, clear signals and mailbox checks (#530).

A SYSTEMIC fault (one that waiting cannot fix) halts Family mail in both
mail consumers of the ``mail-dispatch`` container until it restarts. ADM-13
PR 4a lets an Administrator clear the halt instead, through three records:

- ``MailHalt``: one halt, with the identity the consumer that halted made
  for it, its kind (a refusal before ``DATA`` was sent, or a fault once it
  was) and when it began. A halted consumer records it from its idle loop.
- ``MailHaltClear``: an Administrator's clear signal for one halt. Each
  consumer acts on the signal only while it still holds that halt.
- ``MailboxCheck``: an Administrator's request to check the mailbox sign-in,
  which the ``mail-dispatch`` main process runs (the web holds no Workspace
  credential) and settles with its outcome.

The guards in ``schema/migrations/0115_mail_halt_clear.sql`` are the
boundary; every time is the database's. No row holds a message, recipient,
address, host or credential.
"""

import uuid

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField

# A halt's kind, read from the outcome that caused it: refused before the
# DATA command was sent (definitely unsent, a failed delivery), or a fault
# once it was (Gmail may have accepted it: delivery_unknown).
HALT_KINDS = ("before_data", "after_data")
# A mailbox check's states: waiting for mail-dispatch to claim it, running,
# then the helper's outcome.
CHECK_STATES = ("waiting", "running", "passed", "refused", "unavailable")
CHECK_OUTCOMES = ("passed", "refused", "unavailable")


class MailHalt(models.Model):
    """One halt of Family mail, shared by both mail consumers."""

    id = models.UUIDField(primary_key=True, editable=False)
    began_at = UTCDateTimeField(db_default=Now())
    kind = models.CharField(max_length=16)

    class Meta:
        db_table = "stewardship_mail_halt"
        indexes = [models.Index(models.F("began_at").desc(), name="mail_halt_newest")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=HALT_KINDS), name="mail_halt_kind"
            ),
        ]


class MailHaltClear(models.Model):
    """An Administrator's clear signal for one halt (at most one per halt)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    halt = models.OneToOneField(
        MailHalt, on_delete=models.PROTECT, related_name="clear"
    )
    created_at = UTCDateTimeField(db_default=Now())
    actor_id = models.UUIDField()
    session_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_mail_halt_clear"


class MailboxCheck(models.Model):
    """One mailbox sign-in check an Administrator asked for, and its outcome."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = UTCDateTimeField(db_default=Now())
    actor_id = models.UUIDField()
    session_id = models.UUIDField()
    state = models.CharField(max_length=16, default="waiting", db_default="waiting")
    claimed_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)

    class Meta:
        db_table = "stewardship_mailbox_check"
        indexes = [
            models.Index(models.F("created_at").desc(), name="mailbox_check_newest")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(state__in=CHECK_STATES), name="mailbox_check_state"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    state="waiting", claimed_at__isnull=True, finished_at__isnull=True
                )
                | models.Q(
                    state="running", claimed_at__isnull=False, finished_at__isnull=True
                )
                | models.Q(
                    state__in=CHECK_OUTCOMES,
                    claimed_at__isnull=False,
                    finished_at__isnull=False,
                ),
                name="mailbox_check_shape",
            ),
        ]
