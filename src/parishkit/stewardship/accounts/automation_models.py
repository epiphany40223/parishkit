"""Durable Admin automation sessions, their command logins and their notices.

An automation session lets the host command line act as one Administrator,
who approved it once in a freshly signed-in browser, for at most 30 days (see
the Admin automation specification's "Automation sessions"). The database
holds only the SHA-256 digest of the session secret; the secret itself stays
in an owner-only file on the host. Each command opens a short-lived ordinary
Admin session (a command session), linked here, so every existing
session-bound check applies unchanged.

These tables are created by the forward migration
``accounts/migrations/0004_automation_sessions.py`` and its frozen SQL file,
whose guards, not these declarations, are the authority: who may insert, which
changes an update may make and by which login, and that nothing is deleted
except a command login whose Admin session is already gone. Rows are kept for
attribution; they hold no secret, no Family data and no address.
"""

from django.db import models
from django.db.models.functions import Now

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)

SCOPES = ("read_only", "full")
# Why a session ended. Expiry needs no reason: a row past expires_at is ended.
END_REASONS = (
    "logout",
    "revoked_by_owner",
    "revoked_by_administrator",
    "role_lost",
    "user_removed",
    "recovery",
    "restore",
    "revoked_by_operator",
    "host_mismatch",
    "misused",
    "pairing_abandoned",
)
NOTICE_KINDS = (
    "approved",
    "fresh_gated",
    "irreversible",
    "policy_change",
    "refused",
    "ended",
)
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
# Printable text without control characters; Python also requires
# str.isprintable() before writing (see automation_sessions.valid_label).
LABEL_PATTERN = r"^[^[:cntrl:]]{1,64}$"
EVENT_TYPE_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"


class AutomationSession(MutableRecord):
    """One Administrator-approved automation session.

    ``principal_id`` is the approving Administrator, as whom every command
    acts; ``approving_session_id`` and ``authenticated_at`` record the browser
    session and its Google sign-in behind the approval. Only ``last_used_at``
    moves during the session's life, and ``revoked_at`` with ``end_reason``
    are set once, together, when it ends.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "principal_id",
        "approving_session_id",
        "authenticated_at",
        "expires_at",
        "scope",
        "label",
        "secret_digest",
        "host_digest",
    )
    write_once_fields = ("revoked_at", "end_reason")
    principal_id = models.UUIDField()
    approving_session_id = models.UUIDField()
    authenticated_at = UTCDateTimeField()
    expires_at = UTCDateTimeField()
    scope = models.CharField(max_length=9)
    label = models.CharField(max_length=64)
    secret_digest = models.CharField(max_length=64, unique=True)
    host_digest = models.CharField(max_length=64)
    last_used_at = UTCDateTimeField(null=True, blank=True)
    revoked_at = UTCDateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=32, null=True, blank=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_automation_session"
        indexes = [
            models.Index(
                fields=["principal_id", "revoked_at"],
                name="automation_session_principal",
            ),
        ]
        constraints = MutableRecord.Meta.constraints + [
            models.CheckConstraint(
                condition=models.Q(scope__in=SCOPES), name="automation_session_scope"
            ),
            models.CheckConstraint(
                condition=models.Q(secret_digest__regex=DIGEST_PATTERN)
                & models.Q(host_digest__regex=DIGEST_PATTERN),
                name="automation_session_digests",
            ),
            models.CheckConstraint(
                condition=models.Q(label__regex=LABEL_PATTERN),
                name="automation_session_label",
            ),
            models.CheckConstraint(
                condition=models.Q(expires_at__gt=models.F("authenticated_at")),
                name="automation_session_lifetime",
            ),
            models.CheckConstraint(
                condition=models.Q(revoked_at__isnull=True, end_reason__isnull=True)
                | models.Q(revoked_at__isnull=False, end_reason__in=END_REASONS),
                name="automation_session_ending",
            ),
        ]


class AutomationLogin(models.Model):
    """Links one command session (an ordinary Admin session) to its automation session.

    ``portal_session_id`` is opaque, with no foreign key, so session cleanup
    can delete the Admin session first; the maintenance task then deletes this
    link, which is the only delete its guard admits. Attribution after that
    rests on the command's audit events.
    """

    portal_session_id = models.UUIDField(primary_key=True)
    automation_session = models.ForeignKey(
        AutomationSession, on_delete=models.PROTECT, related_name="logins"
    )
    created_at = UTCDateTimeField(db_default=Now(), editable=False)

    class Meta:
        db_table = "stewardship_automation_login"


class AutomationNotice(ImmutableRecord):
    """One dashboard notice about an automation event, shown to every Administrator.

    It names the session (whose label the page escapes as text), the command
    type and campaign where there is one; never a secret, digest or Family.
    A refused unknown secret has no session.
    """

    kind = models.CharField(max_length=16)
    automation_session = models.ForeignKey(
        AutomationSession,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="notices",
    )
    command_type = models.CharField(max_length=64, null=True, blank=True)
    campaign_id = models.UUIDField(null=True, blank=True)

    class Meta:
        db_table = "stewardship_automation_notice"
        indexes = [
            models.Index(fields=["created_at"], name="automation_notice_created"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(kind__in=NOTICE_KINDS),
                name="automation_notice_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(command_type__isnull=True)
                | models.Q(command_type__regex=EVENT_TYPE_PATTERN),
                name="automation_notice_command_type",
            ),
        ]


class AutomationNoticeAcknowledgement(ImmutableRecord):
    """One Administrator has acknowledged one notice, for their own dashboard only."""

    notice = models.ForeignKey(
        AutomationNotice, on_delete=models.PROTECT, related_name="acknowledgements"
    )
    administrator_id = models.UUIDField()

    class Meta:
        db_table = "stewardship_automation_notice_ack"
        constraints = [
            models.UniqueConstraint(
                fields=["notice", "administrator_id"],
                name="automation_notice_ack_identity",
            ),
        ]
