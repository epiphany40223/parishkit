"""PostgreSQL's connection-source rules (``pg_hba.conf``), from the topology.

Operations spec, "Database connection sources": PostgreSQL accepts each login
only from the network identity of the service that uses it, so a password
read by another container is useless anywhere else. The rules are rendered
from the same Compose document as the services, and each service's login is
the one its own rendered configuration names, so a service, login or address
cannot change in one and not the other.

The file is a rendered document in its own owner-only directory beside the
Compose documents; PostgreSQL mounts that directory read-only and starts with
``-c hba_file=``. The stock ``pg_hba.conf`` in the data directory is never
edited, so a release without this file uses the stock rules again once its
rendering has recreated PostgreSQL.
"""

import re

from parishkit.config import ConfigError

from .inspect_login import INSPECT_LOGIN
from .runtime_network import OFFLINE_PROFILES

HBA_MOUNT = "/etc/postgresql/hba"
HBA_FILE = HBA_MOUNT + "/pg_hba.conf"
OPERATOR = "pk_stewardship_operator"
# The one profile whose service connects as the operator over TCP.
OPERATOR_PROFILE = "database-provision"
# LOCAL's seeder runs web's definition with ``docker run --ip`` at this
# service name's fixed ``backend`` address (local-environment spec, "Seeder
# phases and identities"); it is never a Compose service.
LOCAL_SEEDER = "local-seeder"
_LOGIN = re.compile(r"pk_stewardship_[a-z0-9_]+")
HEADER = (
    "# Rendered by ParishKit Stewardship from the runtime topology; never edit.\n"
    "# Each login is accepted only from its own service's address, only for\n"
    "# this deployment's database, and only with scram-sha-256. The socket is\n"
    "# reachable only through docker exec, and only the operator (any database)\n"
    "# and the inspection login (this deployment's database) may use it.\n"
)


def _database(name):
    """The deployment's database, always quoted so no keyword applies.

    A quoted name in ``pg_hba.conf`` is matched literally, so ``all``,
    ``replication`` or a leading ``+`` or ``@`` never gain their special
    meanings. A leading ``/`` is still a regular expression when quoted
    (PostgreSQL 16 and later), so ``"/.*"`` would match every database; it
    is refused, as are a double quote and a non-printable character, which
    have no safe form.
    """
    if (
        type(name) is not str
        or not name
        or name.startswith("/")
        or '"' in name
        or any(not " " <= character <= "~" for character in name)
    ):
        raise ConfigError("The database name cannot be written as a source rule.")
    return '"' + name + '"'


def held_logins(configuration):
    """The SQL logins one service's own configuration holds.

    Its own ``postgres.user``, plus the download login for web, which holds
    both passwords by design. The renderer derives each service's rules from
    this, and ``database-sources`` its expected row, so the two agree.
    """
    from .deployment import ServiceRole
    from .runtime_grants import login_name

    extra = (
        (login_name("download"),)
        if configuration.service_role is ServiceRole.WEB
        else ()
    )
    return (configuration.postgres.user, *extra)


def _source(network, name, service):
    """The one address range ``name``'s containers can connect from.

    A long-running service has one fixed ``backend`` address. A one-shot
    profile is on its own offline network only, and its containers take
    addresses from that network's upper /29, which excludes the gateway.
    """
    networks = service.get("networks", {})
    if name in OFFLINE_PROFILES:
        if "backend" in networks or f"offline-{name}" not in networks:
            raise ConfigError("A one-shot profile must use only its own network.")
        return network.offline_source(name)
    address = networks.get("backend", {}).get("ipv4_address")
    if address is None:
        raise ConfigError("A database client has no fixed backend address.")
    return address + "/32"


def source_rules(configuration, services, logins, *, seeder=None):
    """The ``(login, source)`` pairs, in rendering order, after every check.

    ``logins`` maps each rendered service name to the SQL logins it holds.
    ``seeder`` is LOCAL's seeder address, allowed only for web's logins.
    The result must cover exactly the login inventory (every login in
    ``database_identities()`` plus the operator), and the operator's only
    TCP source is ``database-provision``.
    """
    from .deployment import DeploymentProfile
    from .runtime_identities import database_identities

    network = configuration.runtime_network
    rules, sources = [], {}
    for name, held in logins.items():
        source = _source(network, name, services[name])
        if sources.setdefault(source, name) != name:
            raise ConfigError("Two database clients share one source address.")
        if OPERATOR in held and (name != OPERATOR_PROFILE or held != (OPERATOR,)):
            raise ConfigError("The operator may connect only from database-provision.")
        rules += [(login, source) for login in held]
    if seeder is not None:
        if configuration.profile is not DeploymentProfile.LOCAL:
            raise ConfigError("The seeder's source exists only in the local profile.")
        if seeder + "/32" in sources:
            raise ConfigError("Two database clients share one source address.")
        rules += [(login, seeder + "/32") for login in logins["web"]]
    inventory = {OPERATOR, *(entry[1] for entry in database_identities())}
    named = [login for login, _ in rules]
    if set(named) != inventory or any(_LOGIN.fullmatch(item) is None for item in named):
        # An inventory login with no source, or a source for an unknown login.
        raise ConfigError("Database logins and connection sources disagree.")
    return rules


def render_hba(configuration, services, logins, *, seeder=None):
    """The complete ``pg_hba.conf`` text: the socket, one line per rule, reject.

    The socket keeps ``trust`` for the operator alone, for every database:
    the image's first-start initialization connects to ``postgres`` as the
    operator through it, and only ``docker exec`` (host root) reaches it.
    The socket also trusts the password-less, SELECT-only inspection login
    (``inspect_login.INSPECT_LOGIN``, #2148) for this deployment's database
    alone. It is deliberately not in ``database_identities()``, so it has no
    TCP rule and ``database-sources`` never tries it over TCP.
    Every TCP line names this deployment's database and ``scram-sha-256``;
    the final ``host all all all reject`` refuses everything else, so a
    refusal always matches a line.
    """
    database = _database(configuration.postgres.name)
    lines = [
        f"local all {OPERATOR} trust",
        f"local {database} {INSPECT_LOGIN} trust",
        "local all all reject",
        *(
            f"host {database} {login} {source} scram-sha-256"
            for login, source in source_rules(
                configuration, services, logins, seeder=seeder
            )
        ),
        "host all all all reject",
    ]
    return HEADER + "".join(line + "\n" for line in lines)
