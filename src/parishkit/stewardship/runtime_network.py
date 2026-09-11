"""Explicit isolated proxy/backend subnets; never trust an entire shared bridge."""

from dataclasses import dataclass, fields
from ipaddress import IPv4Network, ip_network

from parishkit.config import ConfigError


@dataclass(frozen=True)
class RuntimeNetwork:
    """Stable service addresses let application admission identify one proxy hop."""

    backend: str = "172.29.240.0/24"
    proxy: str = "172.29.241.0/24"

    def __post_init__(self):
        """Require disjoint canonical private IPv4 networks with bounded capacity."""
        networks = []
        for value in (self.backend, self.proxy):
            if type(value) is not str:
                raise ConfigError("Runtime network is invalid.")
            try:
                network = ip_network(value, strict=True)
            except (ValueError, TypeError):
                raise ConfigError("Runtime network is invalid.") from None
            if (
                not isinstance(network, IPv4Network)
                or str(network) != value
                or not 24 <= network.prefixlen <= 26
                or not any(
                    network.subnet_of(ip_network(private))
                    for private in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
                )
            ):
                raise ConfigError("Runtime networks require private bounded subnets.")
            networks.append(network)
        if networks[0].overlaps(networks[1]):
            raise ConfigError("Proxy and database networks must be disjoint.")

    @property
    def caddy(self):
        """Only this peer is trusted for normalized forwarding metadata."""
        return str(ip_network(self.proxy)[2])

    def web(self, replica=0):
        """Compose supports up to eight explicitly budgeted web replicas."""
        if type(replica) is not int or not 0 <= replica < 8:
            raise ConfigError("Unsupported web replica identity.")
        return str(ip_network(self.proxy)[10 + replica])


def parse_network(value):
    """No silently ignored network options or arbitrary forwarded-header trust."""
    if type(value) is not dict or value.keys() - {
        item.name for item in fields(RuntimeNetwork)
    }:
        raise ConfigError("Runtime network configuration shape is invalid.")
    return RuntimeNetwork(**value)
