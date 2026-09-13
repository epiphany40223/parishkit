"""Strict local Ministry activity policy, independent of upstream roster facts."""

from parishkit.stewardship.schema_primitives import invalid

SCHEMA = "ministry-activity-v4"
REQUEST_SCHEMA = "ministry-activity-patch-v4"
RECOVERY_SCHEMA = "operator-recovery-ministry-v4"


class MinistryReconciliationUnavailable(RuntimeError):
    """Activity cannot apply without the required source evidence for seeded scope."""


def validate_installation(document):
    """Hold seeded activity edits lacking source before selecting their YAML.

    With current source, activation performs exact seeded reconciliation in its
    transaction. SQL independently requires its receipt even after this preflight.
    """
    from .runtime_models import SystemConfiguration

    runtime = SystemConfiguration.objects.select_related("active_configuration").first()
    old = (
        runtime.active_configuration.canonical_document["sections"].get(
            "ministries", []
        )
        if runtime is not None and runtime.active_configuration_id is not None
        else []
    )
    if old != document["sections"].get("ministries", []) and any(
        row["values"].get("kind") == "assignment"
        and row["values"].get("source") == "chair-seed"
        for row in document["sections"].get("login_rules", [])
    ):
        from parishkit.stewardship.source.snapshot_models import SourceCurrent

        if not SourceCurrent.objects.filter(snapshot__isnull=False).exists():
            raise MinistryReconciliationUnavailable(
                "Ministry activity requires seeded assignment reconciliation."
            )


def validate_records(records):
    """Require explicit activity and unique exact tenant/Ministry identities."""
    seen = set()
    for record in records:
        values = record["values"]
        if set(values) != {"organization_id", "ministry_duid", "active"}:
            invalid()
        identity = (values["organization_id"], values["ministry_duid"])
        if (
            any(type(value) is not int or not 1 <= value < 2**31 for value in identity)
            or type(values["active"]) is not bool
            or identity in seen
        ):
            invalid()
        seen.add(identity)


def remember_records(document, by_id, by_identity):
    """Retired or renamed source Ministries cannot change a retained YAML binding."""
    from parishkit.config import ConfigError

    for record in document["sections"].get("ministries", []):
        values = record["values"]
        identity = (values["organization_id"], values["ministry_duid"])
        identifier = record["id"]
        if (
            by_id.setdefault(identifier, identity) != identity
            or by_identity.setdefault(identity, identifier) != identifier
        ):
            raise ConfigError("Ministry activity identities must remain stable.")


def active_ministries(document, *, organization_id, catalog_duids):
    """Filter a validated policy against actual catalog presence, never names.

    The owning caller supplies its verified applied document and current source
    catalog. Campaign selection is an additional intersection, not a substitute
    for activity. An absent override defaults active; absent source stays absent.
    """
    if (
        type(organization_id) is not int
        or not 1 <= organization_id < 2**31
        or not isinstance(catalog_duids, frozenset)
        or any(
            type(value) is not int or not 1 <= value < 2**31 for value in catalog_duids
        )
    ):
        raise ValueError("Ministry activity requires exact tenant and catalog IDs.")
    records = document["sections"].get("ministries", [])
    validate_records(records)
    inactive = {
        row["values"]["ministry_duid"]
        for row in records
        if row["values"]["organization_id"] == organization_id
        and not row["values"]["active"]
    }
    return catalog_duids - inactive
