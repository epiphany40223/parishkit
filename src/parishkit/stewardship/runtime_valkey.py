"""Exact authenticated Valkey command/key vocabulary for web foundations."""

import hashlib

from parishkit.config import ConfigError

from .deployment import ServiceRole
from .jobs.queues import BROKER_PREFIX, ROLE_QUEUES, WorkQueue


def _password_digest(password):
    """Validate private ACL input without reflecting credential bytes in errors."""
    if (
        type(password) is not bytes
        or not 1 <= len(password) <= 256
        or any(value <= 32 or value >= 127 for value in password)
    ):
        raise ConfigError("The Valkey credential has invalid bytes.")
    return hashlib.sha256(password).hexdigest()


def web_acl(password):
    """Build a private server ACL without placing plaintext credentials in options.

    Only limiter/counter operations are available. INFO is needed for durable
    restart/eviction detection; neither ACL/configuration changes, flushing nor
    background queue keys are granted to web. Consumers read a separate password
    file and never receive this server-only ACL file.
    """
    digest = _password_digest(password)
    commands = (
        "+ping +evalsha +eval +script|load +script|exists +time "
        "+zremrangebyscore +zadd +expire +zremrangebyrank +zcard "
        "+hmget +hset +get +set +exists +del +hincrby +hgetall "
        "+select +client|setinfo +info"
    )
    return (
        "user default off\nuser web on #"
        + digest
        + " ~stewardship:auth:v1:* ~stewardship:ops:v1:* "
        + commands
        + "\n"
    ).encode("ascii")


def broker_acl(service, password):
    """Grant only reviewed transport commands and this service's queue namespace.

    Publishers cannot consume or remove queued work. Consumers can restore their
    own unacknowledged messages but cannot touch another service's queues, web
    limiter state, server configuration or ACLs. Remote control/gossip/fanout
    are deliberately absent from both the runtime and this command vocabulary.
    """
    if not isinstance(service, ServiceRole) or service not in ROLE_QUEUES:
        raise ConfigError("This service has no background Valkey authority.")
    digest = _password_digest(password)
    producer = service is ServiceRole.SCHEDULER
    queues = frozenset(WorkQueue) if producer else ROLE_QUEUES[service]
    keys = []
    for queue in sorted(queues):
        # Kombu appends a priority separator to list names. Prefixes here are
        # fixed disjoint reviewed queue names, never administrator input.
        keys.extend(
            (
                f"~{BROKER_PREFIX}{queue.value}*",
                f"~{BROKER_PREFIX}_kombu.binding.{queue.value}",
            )
        )
    commands = (
        "+ping +select +client|setinfo +llen +exists "
        "+sadd +smembers +lpush +multi +exec"
    )
    if not producer:
        keys.append(f"~{BROKER_PREFIX}{service.value}:unacked*")
        commands += (
            " +brpop +rpop +srem +hset +hget +hdel +zadd +zrem +zrevrangebyscore"
            " +get +set +del +expire +pexpire +watch +unwatch"
            " +eval +evalsha +script|load +script|exists"
        )
    return (
        f"user {service.value} on #{digest} " + " ".join(keys) + " " + commands + "\n"
    ).encode("ascii")


def server_acl(passwords):
    """Compose only explicitly supplied service credentials; default stays disabled."""
    if (
        type(passwords) is not dict
        or ServiceRole.WEB not in passwords
        or any(
            not isinstance(role, ServiceRole)
            or role not in {ServiceRole.WEB, *ROLE_QUEUES}
            for role in passwords
        )
    ):
        raise ConfigError("The Valkey service credential inventory is invalid.")
    result = web_acl(passwords[ServiceRole.WEB])
    for role in sorted(passwords):
        if role is not ServiceRole.WEB:
            result += broker_acl(role, passwords[role])
    return result
