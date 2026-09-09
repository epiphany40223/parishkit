"""Immutable YAML policy projections and separately mutable Google identities."""

from django.db import models

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


class AddressRule(PolicyProjection):
    """Exact-email overrides include explicit denial and immutable creation origin."""

    email = models.EmailField()
    roles = models.JSONField()
    creation_origin = models.CharField(max_length=16)
    creation_operation = models.UUIDField()

    class Meta(PolicyProjection.Meta):
        db_table = "stewardship_address_rule"
        constraints = PolicyProjection.Meta.constraints + [
            models.UniqueConstraint(
                fields=["configuration", "email"], name="address_rule_identity"
            )
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
    """Google's stable subject owns identity, never a mutable email address."""

    immutable_fields = MutableRecord.immutable_fields + ("google_subject",)
    google_subject = models.CharField(max_length=255, unique=True)
    email = models.EmailField(db_index=True)
    hosted_domain = models.CharField(max_length=253, null=True)
    verified_at = UTCDateTimeField()
    disabled = models.BooleanField(default=False)

    class Meta(MutableRecord.Meta):
        db_table = "stewardship_portal_user"


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
    """Durable expansion alert/notification intent, independent of delivery."""

    activation = models.ForeignKey("ConfigurationActivation", on_delete=models.PROTECT)
    rule_record_id = models.UUIDField()
    target = models.CharField(max_length=254)
    kind = models.CharField(max_length=32)
    before_roles = models.JSONField()
    after_roles = models.JSONField()
    recipients = models.JSONField()

    class Meta:
        db_table = "stewardship_policy_security_event"
        constraints = [
            models.UniqueConstraint(
                fields=["activation", "rule_record_id"],
                name="policy_security_event_identity",
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
