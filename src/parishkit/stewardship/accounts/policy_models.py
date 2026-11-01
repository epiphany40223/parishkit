"""Immutable YAML policy projections and separately mutable Google identities."""

from django.db import models
from django.db.models.functions import Lower, Trim
from django.db.models.lookups import Exact

from parishkit.stewardship.storage import (
    ImmutableRecord,
    MutableRecord,
    UTCDateTimeField,
)


class PolicyProjection(ImmutableRecord):
    """Stable YAML record identity within a protected immutable configuration."""

    configuration = models.ForeignKey(
        "AppliedConfigurationVersion", on_delete=models.PROTECT
    )
    record_id = models.UUIDField()

    class Meta:
        abstract = True
        constraints = [
            models.UniqueConstraint(
                fields=["configuration", "record_id"], name="%(class)s_record"
            )
        ]


class DomainRule(PolicyProjection):
    """Workspace hosted-domain policy; suffix-only matching is never sufficient."""

    domain = models.CharField(max_length=253)
    roles = models.JSONField()

    class Meta(PolicyProjection.Meta):
        db_table = "stewardship_domain_rule"
        constraints = PolicyProjection.Meta.constraints + [
            models.UniqueConstraint(
                fields=["configuration", "domain"], name="domain_rule_identity"
            )
        ]


def _user_shape(role, roles):
    """One users-form row (#952): a Name, this role, its cumulative roles."""
    return models.Q(
        role=role,
        roles=roles,
        name__isnull=False,
        creation_origin__isnull=True,
        creation_operation__isnull=True,
    )


class AddressRule(PolicyProjection):
    """One address's sign-in rule: an older exact-address rule, or a user.

    Older rows (configurations before the users upgrade, #952) carry a
    creation origin and operation, may hold any role set including none (an
    explicit deny) and have no Name or role. A user row carries a Name and
    one ``role``, and ``roles`` is that role's cumulative set, so every SQL
    check of ``roles`` keeps its meaning; it has no origin and no grants.
    ``address_rule_shape`` keeps the two shapes apart and ties ``roles`` to
    ``role``.
    """

    email = models.EmailField()
    roles = models.JSONField()
    creation_origin = models.CharField(max_length=16, null=True)
    creation_operation = models.UUIDField(null=True)
    name = models.CharField(max_length=200, null=True)
    role = models.CharField(max_length=32, null=True)

    class Meta(PolicyProjection.Meta):
        db_table = "stewardship_address_rule"
        constraints = PolicyProjection.Meta.constraints + [
            models.UniqueConstraint(
                fields=["configuration", "email"], name="address_rule_identity"
            ),
            models.CheckConstraint(
                condition=models.Q(
                    role__isnull=True,
                    name__isnull=True,
                    creation_origin__isnull=False,
                    creation_operation__isnull=False,
                )
                | _user_shape("ministry_leader", ["ministry_leader"])
                | _user_shape("staff", ["ministry_leader", "staff"])
                | _user_shape(
                    "administrator", ["administrator", "ministry_leader", "staff"]
                ),
                name="address_rule_shape",
            ),
        ]


class AddressRoleGrant(ImmutableRecord):
    """Each configured role retains every explicitly granted provenance origin."""

    rule = models.ForeignKey(
        AddressRule, on_delete=models.PROTECT, related_name="grants"
    )
    role = models.CharField(max_length=32)
    origins = models.JSONField()

    class Meta:
        db_table = "stewardship_address_grant"
        constraints = [
            models.UniqueConstraint(fields=["rule", "role"], name="address_grant_role")
        ]


class MinistryAssignment(PolicyProjection):
    """Configured assignment, separate from source-driven suppression."""

    email = models.EmailField()
    ministry_duid = models.PositiveBigIntegerField()
    source = models.CharField(max_length=16)
    operation_id = models.UUIDField()

    class Meta(PolicyProjection.Meta):
        db_table = "stewardship_ministry_assignment"
        constraints = PolicyProjection.Meta.constraints + [
            models.UniqueConstraint(
                fields=["configuration", "email", "ministry_duid", "source"],
                name="ministry_assignment_identity",
            )
        ]


class PortalUser(MutableRecord):
    """Google's stable subject owns identity, never a mutable email address.

    ``sign_in_email`` is Google's ``email`` claim from the account's latest
    verified sign-in; ``email`` is the address the account acts as, which
    differs from it only while a usable verified-address match of the
    account names it (#2343; data spec "Administration user and policy").
    """

    immutable_fields = MutableRecord.immutable_fields + ("google_subject",)
    google_subject = models.CharField(max_length=255, unique=True)
    email = models.EmailField(db_index=True)
    sign_in_email = models.EmailField()
    hosted_domain = models.CharField(max_length=253, null=True)
    verified_at = UTCDateTimeField()
    disabled = models.BooleanField(default=False)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_portal_user"

    def save(self, *args, **kwargs):
        """A new account signs in with the address it acts as.

        The insert guard requires the two to be equal on a new row, so an
        insert that names only ``email`` gets it as the sign-in address too.
        """
        if self._state.adding and not self.sign_in_email:
            self.sign_in_email = self.email
        return super().save(*args, **kwargs)


# How a verified-address match ended (data spec "Verified-address match
# records"); the match guard re-derives each from the tables.
ADDRESS_MATCH_END_REASONS = (
    "sign_in_address_matches",
    "not_confirmed",
    "user_gone",
    "replaced",
    "ended_by_administrator",
)


def _trimmed_lower(field):
    """The check that an address column is stored trimmed and lower case."""
    return models.Q((field, Lower(Trim(field))))


class AddressMatch(MutableRecord):
    """One Google account matched to one user's address that Google verified.

    Runtime identity state made only by the person's own second sign-in step
    (#2343). The SQL guard ``stewardship_address_match_guard_v1`` decides
    every insert, re-check and end; this model only declares the shape.
    """

    immutable_fields = MutableRecord.immutable_fields + (
        "portal_user",
        "email",
        "user_record_id",
        "sign_in_email",
        "matched_at",
    )
    write_once_fields = ("ended_at", "end_reason", "ended_by_id", "ended_session_id")
    portal_user = models.ForeignKey(
        PortalUser, on_delete=models.PROTECT, related_name="+"
    )
    email = models.EmailField()
    user_record_id = models.UUIDField()
    sign_in_email = models.EmailField()
    matched_at = UTCDateTimeField()
    confirmed_at = UTCDateTimeField()
    ended_at = UTCDateTimeField(null=True)
    end_reason = models.CharField(max_length=32, null=True)
    ended_by_id = models.UUIDField(null=True)
    ended_session_id = models.UUIDField(null=True)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_address_match"
        constraints = MutableRecord.Meta.constraints + [
            models.UniqueConstraint(
                fields=["portal_user"],
                condition=models.Q(ended_at__isnull=True),
                name="address_match_one_per_account",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=models.Q(ended_at__isnull=True),
                name="address_match_one_per_address",
            ),
            models.CheckConstraint(
                condition=_trimmed_lower("email") & _trimmed_lower("sign_in_email"),
                name="address_match_normalized",
            ),
            models.CheckConstraint(
                condition=~models.Q(email=models.F("sign_in_email")),
                name="address_match_addresses_differ",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    ended_at__isnull=True,
                    end_reason__isnull=True,
                    ended_by_id__isnull=True,
                    ended_session_id__isnull=True,
                )
                | models.Q(
                    ended_at__isnull=False,
                    end_reason__isnull=False,
                    end_reason="ended_by_administrator",
                    ended_by_id__isnull=False,
                    ended_session_id__isnull=False,
                )
                | models.Q(
                    ended_at__isnull=False,
                    end_reason__isnull=False,
                    end_reason__in=ADDRESS_MATCH_END_REASONS[:4],
                    ended_by_id__isnull=True,
                    ended_session_id__isnull=True,
                ),
                name="address_match_end",
            ),
        ]
        indexes = [
            models.Index(
                fields=["email", "matched_at"], name="address_match_email_time"
            )
        ]


class AssignmentOverlay(MutableRecord):
    """Latest promoted-source decision for a stable seeded assignment ID."""

    immutable_fields = MutableRecord.immutable_fields + ("assignment_record_id",)
    assignment_record_id = models.UUIDField(unique=True)
    active = models.BooleanField()
    source_snapshot_id = models.UUIDField()
    reason = models.CharField(max_length=32)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_assignment_overlay"


class PolicySecurityEvent(ImmutableRecord):
    """Durable expansion alert/notification intent, independent of delivery.

    An event comes from a configuration activation, or (``address_matched``,
    #2343) from a new verified-address match, never both. A match's event
    also names the matched Google account's sign-in address and, when the
    match took the address over from another account, that account's
    sign-in address and last sign-in.
    """

    activation = models.ForeignKey(
        "ConfigurationActivation", on_delete=models.PROTECT, null=True
    )
    address_match = models.OneToOneField(
        AddressMatch, on_delete=models.PROTECT, null=True, related_name="+"
    )
    rule_record_id = models.UUIDField()
    target = models.CharField(max_length=254)
    kind = models.CharField(max_length=32)
    before_roles = models.JSONField()
    after_roles = models.JSONField()
    recipients = models.JSONField()
    account_email = models.EmailField(null=True)
    replaced_account_email = models.EmailField(null=True)
    replaced_login_at = UTCDateTimeField(null=True)

    class Meta:
        db_table = "stewardship_policy_security_event"
        constraints = [
            models.UniqueConstraint(
                fields=["activation", "rule_record_id"],
                name="policy_security_event_identity",
            ),
            models.CheckConstraint(
                condition=Exact(
                    models.Func(
                        models.F("activation"),
                        models.F("address_match"),
                        function="num_nonnulls",
                        output_field=models.IntegerField(),
                    ),
                    1,
                ),
                name="policy_security_event_source",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        kind="address_matched",
                        address_match__isnull=False,
                        account_email__isnull=False,
                    )
                    & _trimmed_lower("account_email")
                )
                | (
                    ~models.Q(kind="address_matched")
                    & models.Q(
                        address_match__isnull=True,
                        account_email__isnull=True,
                        replaced_account_email__isnull=True,
                    )
                ),
                name="policy_security_event_account",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    replaced_account_email__isnull=True, replaced_login_at__isnull=True
                )
                | (
                    models.Q(replaced_account_email__isnull=False)
                    & _trimmed_lower("replaced_account_email")
                ),
                name="policy_security_event_replaced",
            ),
        ]


class PolicySecurityAcknowledgement(ImmutableRecord):
    """One Administrator's acknowledgement of an expansion, by the address held then.

    The acknowledging identity is the audit actor; its address is what the event's
    recipients are keyed on, since several Google identities may share one
    address and an address may change. Whether the acknowledgement is the
    granting actor's own is decided when it is recorded, so a later change of
    address cannot turn it into another Administrator's.
    """

    event = models.ForeignKey(
        PolicySecurityEvent, on_delete=models.PROTECT, related_name="acknowledgements"
    )
    email = models.EmailField()
    own = models.BooleanField()

    class Meta:
        db_table = "stewardship_policy_security_ack"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "email"], name="policy_security_ack_identity"
            )
        ]


class PolicyEpoch(ImmutableRecord):
    """Namespace generations prevent old denied-login counters blocking new grants."""

    activation = models.OneToOneField(
        "ConfigurationActivation", on_delete=models.PROTECT
    )
    sequence = models.PositiveBigIntegerField(unique=True)

    class Meta:
        db_table = "stewardship_policy_epoch"


class AdminRevocation(ImmutableRecord):
    """Operator activation invalidates all older Admin OAuth/session generations."""

    activation = models.OneToOneField(
        "ConfigurationActivation", on_delete=models.PROTECT
    )

    class Meta:
        db_table = "stewardship_admin_revocation"
