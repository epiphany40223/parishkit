"""Real PostgreSQL identities admit target-specific credential queue operations."""

from django.db import connection

from parishkit.config import ConfigError

from .secret_models import SECRET_TARGETS


def _identity(expected):
    """Reject superusers, SET ROLE impersonation and any inherited role authority."""
    if connection.vendor != "postgresql":
        raise ConfigError("Credential services require PostgreSQL isolation.")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT current_user, session_user, rolsuper, rolbypassrls, rolcreatedb, "
            "rolcreaterole, rolreplication, rolinherit, "
            "EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r.oid), "
            "has_schema_privilege(current_user,'public','CREATE') "
            "FROM pg_roles r WHERE rolname=current_user"
        )
        row = cursor.fetchone()
    if row is None or row[:2] != (expected, expected) or any(row[2:]):
        raise ConfigError("Credential service database identity is not isolated.")


def admit_installer_database(target):
    """Check actual login and grants on every queue operation, including reconnects.

    RLS supplies target scoping; deployment provisioning owns these narrow grants.
    The online installer cannot be a table owner, read campaign answers, inspect
    audit payloads, or read its web-readable staging store through a bypass role.
    """
    if type(target) is not str or target not in SECRET_TARGETS:
        raise ConfigError("Unknown credential target.")
    _identity("pk_stewardship_credential_" + target)
    allowed = {
        "stewardship_secret_request": {"SELECT", "UPDATE"},
        "stewardship_secret_checkpoint": {"SELECT", "INSERT"},
        "stewardship_sealed_credential_staging": {"SELECT", "UPDATE"},
        "stewardship_credential_consumer_ack": {"SELECT"},
        "stewardship_audit_event": {"INSERT"},
        "stewardship_system_configuration": {"SELECT"},
        "stewardship_parish": {"SELECT"},
    }
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT c.relname, p, c.relowner=(SELECT oid FROM pg_roles "
            "WHERE rolname=current_user) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "CROSS JOIN unnest(ARRAY['SELECT','INSERT','UPDATE','DELETE',"
            "'TRUNCATE','REFERENCES','TRIGGER']) p "
            "WHERE n.nspname='public' AND c.relkind IN('r','p','v','m','f') "
            "AND (has_table_privilege(current_user,c.oid,p) OR "
            "CASE WHEN p IN('SELECT','INSERT','UPDATE','REFERENCES') "
            "THEN has_any_column_privilege(current_user,c.oid,p) ELSE false END)"
        )
        for table, privilege, owner in cursor.fetchall():
            if owner or privilege not in allowed.get(table, set()):
                raise ConfigError("Credential installer database grants are excessive.")
        # Metadata attribution is deliberately column-scoped. No full YAML,
        # testing recipient, configuration content or provider settings are needed.
        cursor.execute(
            "SELECT c.relname,a.attname FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "JOIN pg_attribute a ON a.attrelid=c.oid "
            "WHERE n.nspname='public' AND c.relname IN "
            "('stewardship_system_configuration','stewardship_parish') "
            "AND a.attnum>0 AND NOT a.attisdropped "
            "AND has_column_privilege(current_user,c.oid,a.attnum,'SELECT')"
        )
        permitted = {
            ("stewardship_system_configuration", "active_configuration_id"),
            ("stewardship_parish", "id"),
            ("stewardship_parish", "configuration_id"),
        }
        if set(cursor.fetchall()) - permitted:
            raise ConfigError("Credential installer metadata grants are excessive.")


def admit_consumer_database(consumer):
    """A consumer attests as its own authenticated login, never as an installer."""
    from parishkit.stewardship.service_boundaries import ALLOWED_SECRETS

    if type(consumer) is not str or consumer not in {
        role.value for role in ALLOWED_SECRETS
    }:
        raise ConfigError("Unknown credential consumer.")
    _identity("pk_stewardship_" + consumer.replace("-", "_"))
