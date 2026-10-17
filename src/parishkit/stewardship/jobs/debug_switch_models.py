"""The debug-off switch: turn debug logging off in every process (ADM-13, #530).

Debug logging (``PARISHKIT_DEBUG_LOGGING=1``) keeps free text and error
details that can hold personal data. It is set in the server's environment, so
before this switch only recreating the services could turn it off. A set
switch (a row whose ``cleared_at`` is empty) makes every process that reaches
the database stop logging in debug form (``parishkit.stewardship.debug_switch``)
until it is cleared.

The guard in ``schema/migrations/0078_debug_off_switch.sql`` admits a new
switch only from the web login, for an enabled Administrator's live session
(of any sign-in age since 0113), and only while none is set; it lets a switch be
cleared once and never deleted. Every time is the database's. Processes read
it only through ``stewardship_debug_logging_off_v1()``, which answers one
boolean. The server operator clears it with ``pk-stewardship
debug-off-clear`` (``cleared_by`` "operator"); an Administrator may clear it
in Testing mode only ("administrator", with ``cleared_actor_id``).
"""

import uuid

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import UTCDateTimeField

# Who cleared a switch: the server operator's offline command, or (in
# Testing mode only) an Administrator from System health.
CLEARED_BY = ("operator", "administrator")


class DebugOffSwitch(models.Model):
    """One setting of the debug-off switch, and when and by whom it was cleared."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    set_at = UTCDateTimeField(db_default=Now())
    actor_id = models.UUIDField()
    session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    cleared_at = UTCDateTimeField(null=True)
    cleared_by = models.CharField(max_length=16, null=True)
    cleared_actor_id = models.UUIDField(null=True)

    class Meta:
        db_table = "stewardship_debug_off_switch"
        indexes = [
            models.Index(models.F("set_at").desc(), name="debug_off_switch_newest")
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(
                    cleared_at__isnull=True,
                    cleared_by__isnull=True,
                    cleared_actor_id__isnull=True,
                )
                | models.Q(
                    cleared_at__isnull=False,
                    cleared_by="operator",
                    cleared_actor_id__isnull=True,
                )
                | models.Q(
                    cleared_at__isnull=False,
                    cleared_by="administrator",
                    cleared_actor_id__isnull=False,
                ),
                name="debug_off_switch_cleared",
            ),
        ]
