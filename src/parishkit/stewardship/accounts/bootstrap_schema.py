"""Minimal initial authority before the parish/setup wizard has committed.

This separate historical discriminator avoids inventing a parish, phone or logo
just to admit the initial Google Administrator. Successors of this shape exist
for additive offline account recovery while setup remains incomplete. The
explicit initial-setup request format alone adds the complete parish and draft
campaign; ordinary public-setting patches cannot reinterpret this minimal base.
"""

from uuid import UUID, uuid5

from parishkit.config import ConfigError

BOOTSTRAP_SCHEMA = "bootstrap-policy-v1"


def bootstrap_version(deployment_id, admin_email):
    """Construct stable initial IDs so a matching interrupted setup can resume."""
    from .authority import parse_version
    from .policy_schema import normalized_email

    if not isinstance(deployment_id, UUID):
        raise ConfigError("Bootstrap requires an explicit deployment UUID.")
    operation = str(uuid5(deployment_id, "bootstrap-operation-v1"))
    return parse_version(
        {
            "schema_version": 1,
            "version_id": str(uuid5(deployment_id, "bootstrap-authority-v1")),
            "predecessor_digest": None,
            "sections": {
                "login_rules": [
                    {
                        "id": str(uuid5(deployment_id, "bootstrap-admin-rule-v1")),
                        "values": {
                            "kind": "address",
                            "email": normalized_email(admin_email),
                            "roles": ["administrator"],
                            "creation_origin": "manual",
                            "creation_operation": operation,
                            "grants": {"administrator": {"manual": operation}},
                        },
                    }
                ]
            },
        },
        validate_sections=validate_bootstrap_sections,
    )


def validate_bootstrap_sections(document):
    """Permit only manual exact-address Admin rules; a root has exactly one."""
    from .policy_schema import validate_policy_records

    sections = document["sections"]
    if set(sections) != {"login_rules"}:
        raise ConfigError("Bootstrap authority contains unsupported configuration.")
    records = sections["login_rules"]
    validate_policy_records(records)
    if not records or (document["predecessor_digest"] is None and len(records) != 1):
        raise ConfigError("Bootstrap authority requires its initial Administrator.")
    for record in records:
        values = record["values"]
        if (
            values["kind"] != "address"
            or values["roles"] != ["administrator"]
            or values["creation_origin"] != "manual"
            or set(values["grants"]["administrator"]) != {"manual"}
        ):
            raise ConfigError("Bootstrap authority requires exact manual Admin grants.")
