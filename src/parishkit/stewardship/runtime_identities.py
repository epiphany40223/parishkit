"""Closed foundation SQL identity inventory shared by mounts and provisioning."""

from .deployment import SECRET_NAMES, ServiceRole
from .runtime_grants import login_name


def database_identities():
    """Return path-name, SQL-name, role, target tuples without initializing Django."""
    result = [("migration", "pk_stewardship_migration", ServiceRole.MIGRATION, None)]
    for role in (
        ServiceRole.WEB,
        ServiceRole.WORKER,
        ServiceRole.SCHEDULER,
        ServiceRole.CONFIG_INSTALLER,
        ServiceRole.BOOTSTRAP,
        ServiceRole.ADMIN_RECOVERY,
    ):
        result.append((role.value, login_name(role), role, None))
    result.append(("download", login_name("download"), "download", None))
    for target in sorted(SECRET_NAMES - {"handoff_private"}):
        role = ServiceRole.CREDENTIAL_INSTALLER
        result.append(
            (
                "credential-installer-" + target.replace("_", "-"),
                login_name(role, target=target),
                role,
                target,
            )
        )
    return tuple(result)
