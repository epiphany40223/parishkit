"""Minimal initial authority before the parish/setup wizard has committed.

This separate historical discriminator avoids inventing a parish, phone or logo
just to admit the initial Google Administrator. Successors of this shape exist
for additive offline account recovery while setup remains incomplete. The
explicit initial-setup request format alone adds the complete parish and draft
campaign; ordinary public-setting patches cannot reinterpret this minimal base.

Two forms exist. ``bootstrap-policy-v1`` holds exact-address Administrator
sign-in rules; every deployment bootstrapped before the users form (#952)
has one. ``bootstrap-users-v10`` (#993), which ``bootstrap --admin-email``
writes now, holds Administrator users instead, so the setup wizard's
``initial-setup-users-v10`` request finishes in the users form and the
deployment needs no ``upgrade-users`` after setup.
"""

from uuid import UUID, uuid5

from parishkit.config import ConfigError

BOOTSTRAP_SCHEMA = "bootstrap-policy-v1"
USERS_BOOTSTRAP_SCHEMA = "bootstrap-users-v10"
# Every bootstrap form: the configuration of a deployment still in setup.
BOOTSTRAP_SCHEMAS = frozenset({BOOTSTRAP_SCHEMA, USERS_BOOTSTRAP_SCHEMA})


def bootstrap_version(deployment_id, admin_email):
    """Construct stable initial IDs so a matching interrupted setup can resume.

    The one record is the initial Administrator user (#993), whose Name is
    the address's local part until someone renames them on the Users page.
    """
    from .authority import parse_version
    from .policy_schema import normalized_email
    from .users_schema import local_part_name

    if not isinstance(deployment_id, UUID):
        raise ConfigError("Bootstrap requires an explicit deployment UUID.")
    email = normalized_email(admin_email)
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
                            "kind": "user",
                            "email": email,
                            "name": local_part_name(email),
                            "role": "administrator",
                        },
                    }
                ]
            },
        },
        validate_sections=validate_users_bootstrap_sections,
    )


def _login_rules(document, validate_records):
    """The bootstrap's records: only login rules, exactly one at the root.

    ``validate_records`` checks the records' own shape first, as each form
    defines it.
    """
    sections = document["sections"]
    if set(sections) != {"login_rules"}:
        raise ConfigError("Bootstrap authority contains unsupported configuration.")
    records = sections["login_rules"]
    validate_records(records)
    if not records or (document["predecessor_digest"] is None and len(records) != 1):
        raise ConfigError("Bootstrap authority requires its initial Administrator.")
    return records


def validate_bootstrap_sections(document):
    """Permit only manual exact-address Admin rules; a root has exactly one."""
    from .policy_schema import validate_policy_records

    for record in _login_rules(document, validate_policy_records):
        values = record["values"]
        if (
            values["kind"] != "address"
            or values["roles"] != ["administrator"]
            or values["creation_origin"] != "manual"
            or set(values["grants"]["administrator"]) != {"manual"}
        ):
            raise ConfigError("Bootstrap authority requires exact manual Admin grants.")


def validate_users_bootstrap_sections(document):
    """Permit only Administrator users (#993); a root has exactly one.

    Each record is a user as the users form defines one (unique normalized
    address, a valid Name), and each is an Administrator: before setup
    finishes no one else has anything to do, and offline recovery adds only
    Administrators.
    """
    from .users_schema import validate_user_records

    records = _login_rules(document, validate_user_records)
    if any(record["values"]["role"] != "administrator" for record in records):
        raise ConfigError("Bootstrap authority requires Administrator users.")
