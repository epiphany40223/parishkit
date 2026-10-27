"""Explicit isolated proxy/backend subnets; never trust an entire shared bridge.

The addresses here are also PostgreSQL's source identities (operations spec,
"Database connection sources"): every ``backend`` member has a fixed address
in the lower half of the subnet, Docker's dynamic allocations are confined to
the upper half, and each one-shot profile has its own small ``offline``
subnet whose container range excludes the gateway.
"""

from dataclasses import dataclass, fields
from ipaddress import IPv4Network, ip_network

from parishkit.config import ConfigError

PRIVATE_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
DEFAULT_OFFLINE = "172.29.242.0/24"
# The one-shot profiles' own networks, one /28 slice of ``offline`` each, in
# this fixed order: appending keeps every earlier profile's subnet.
OFFLINE_PROFILES = (
    "bootstrap",
    "migration",
    "admin-recovery",
    "database-provision",
    "backup-worker",
)
# Inside each /28: the gateway at offset 1, PostgreSQL at offset 2, and the
# profile's containers only from the upper /29 (offsets 8 to 15).
OFFLINE_PREFIX = 28


def _private_network(value, smallest, largest):
    """One canonical private IPv4 network whose prefix is in the given bounds."""
    if type(value) is not str:
        raise ConfigError("Runtime network is invalid.")
    try:
        network = ip_network(value, strict=True)
    except (ValueError, TypeError):
        raise ConfigError("Runtime network is invalid.") from None
    if (
        not isinstance(network, IPv4Network)
        or str(network) != value
        or not smallest <= network.prefixlen <= largest
        or not any(network.subnet_of(ip_network(item)) for item in PRIVATE_NETWORKS)
    ):
        raise ConfigError("Runtime networks require private bounded subnets.")
    return network


@dataclass(frozen=True)
class RuntimeNetwork:
    """Stable service addresses let application admission identify one proxy hop."""

    backend: str = "172.29.240.0/24"
    proxy: str = "172.29.241.0/24"
    # A /24 holds 16 one-shot slices and a /25 eight; five are used.
    offline: str = DEFAULT_OFFLINE

    def __post_init__(self):
        """Require disjoint canonical private IPv4 networks with bounded capacity."""
        networks = [
            _private_network(self.backend, 24, 26),
            _private_network(self.proxy, 24, 26),
            _private_network(self.offline, 24, 25),
        ]
        if networks[0].overlaps(networks[1]):
            raise ConfigError("Proxy and database networks must be disjoint.")
        if any(networks[2].overlaps(network) for network in networks[:2]):
            raise ConfigError("The offline network must be disjoint from the others.")

    @property
    def caddy(self):
        """Only this peer is trusted for normalized forwarding metadata."""
        return str(ip_network(self.proxy)[2])

    def web(self, replica=0):
        """Compose supports up to eight explicitly budgeted web replicas."""
        if type(replica) is not int or not 0 <= replica < 8:
            raise ConfigError("Unsupported web replica identity.")
        return str(ip_network(self.proxy)[10 + replica])

    def backend_address(self, offset):
        """A fixed ``backend`` address: offset 2 upward, inside the lower half.

        Offset 0 is the network address and offset 1 the gateway. The upper
        half is the dynamic range (below), so no fixed address can ever be
        handed to a container started without one.
        """
        network = ip_network(self.backend)
        if type(offset) is not int or not 2 <= offset < network.num_addresses // 2:
            raise ConfigError("A backend address is outside its fixed range.")
        return str(network[offset])

    @property
    def backend_gateway(self):
        """Pinned at offset 1, so an explicit ``ip_range`` cannot move it."""
        return str(ip_network(self.backend)[1])

    @property
    def backend_dynamic_range(self):
        """The upper half: the only addresses Docker allocates dynamically.

        No source rule allows any address in it.
        """
        return str(list(ip_network(self.backend).subnets(prefixlen_diff=1))[1])

    def offline_subnet(self, profile):
        """The one-shot profile's own /28, a fixed slice of ``offline``."""
        if profile not in OFFLINE_PROFILES:
            raise ConfigError("Unknown one-shot network profile.")
        slices = ip_network(self.offline).subnets(new_prefix=OFFLINE_PREFIX)
        return list(slices)[OFFLINE_PROFILES.index(profile)]

    def offline_ipam(self, profile):
        """The /28's IPAM: gateway at 1, containers only from the upper /29."""
        subnet = self.offline_subnet(profile)
        return {
            "subnet": str(subnet),
            "gateway": str(subnet[1]),
            "ip_range": self.offline_source(profile),
        }

    def offline_postgres(self, profile):
        """PostgreSQL's fixed address on the profile's network (offset 2)."""
        return str(self.offline_subnet(profile)[2])

    def offline_source(self, profile):
        """The upper /29 the profile's containers use; the gateway is never in it."""
        return str(list(self.offline_subnet(profile).subnets(prefixlen_diff=1))[1])


def parse_network(value):
    """No silently ignored network options or arbitrary forwarded-header trust."""
    if type(value) is not dict or value.keys() - {
        item.name for item in fields(RuntimeNetwork)
    }:
        raise ConfigError("Runtime network configuration shape is invalid.")
    return RuntimeNetwork(**value)


def network_document(network):
    """The recorded ``runtime_network``, with ``offline`` only when it is set.

    The previous release's loader refuses an unknown ``offline`` key, so the
    provisioning record and every service document carry it only when the
    deployment chose a non-default block. A deployment that does not set it
    then renders exactly the documents it rendered before the setting existed,
    and the previous release's ``retarget-image`` still accepts its record.
    """
    result = {"backend": network.backend, "proxy": network.proxy}
    if network.offline != DEFAULT_OFFLINE:
        result["offline"] = network.offline
    return result
