"""Explicit operator-owned disposable fixture, never a real deployment provisioner."""

from dataclasses import replace
from uuid import uuid4

from parishkit.stewardship.accounts.key_files import write_private
from parishkit.stewardship.bootstrap import (
    HANDOFF_TARGETS,
    INITIAL_TARGETS,
    BootstrapIdentity,
)
from parishkit.stewardship.deployment import ServiceRole, load_deployment
from parishkit.stewardship.runtime_paths import RuntimeLayout
from parishkit.stewardship.startup_interlock import MARKER


def bootstrap_fixture(root):
    """Pre-provision owner-only directories, stable lease and fake readonly inputs."""
    configuration = load_deployment(environ={"PARISHKIT_ROOT": str(root)})
    layout = RuntimeLayout(configuration)
    oauth, password = (
        layout.credential("google_oauth"),
        layout.database_password("bootstrap"),
    )
    configuration = replace(
        configuration,
        service_role=ServiceRole.BOOTSTRAP,
        postgres=replace(configuration.postgres, password_file=password),
        secrets={"google_oauth": oauth},
    )
    layout = RuntimeLayout(configuration)
    directories = [
        layout.deployment_directory,
        configuration.paths["authority"],
        layout.interlock.parent,
        oauth.parent,
        password.parent,
        *(layout.credential_directory(target) for target in INITIAL_TARGETS),
        *(layout.handoff(target).parent for target in HANDOFF_TARGETS),
    ]
    for path in directories:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    write_private(layout.interlock, MARKER)
    write_private(oauth, b'{"client_id":"fake","client_secret":"fake"}')
    write_private(password, b"disposable-test-only")
    return configuration, BootstrapIdentity(uuid4(), "admin@example.org")
