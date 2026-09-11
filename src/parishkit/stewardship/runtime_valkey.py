"""Exact authenticated Valkey command/key vocabulary for web foundations."""

import hashlib

from parishkit.config import ConfigError


def web_acl(password):
    """Build a private server ACL without placing plaintext credentials in options.

    Only limiter/counter operations are available. INFO is needed for durable
    restart/eviction detection; neither ACL/configuration changes, flushing nor
    background queue keys are granted to web. Consumers read a separate password
    file and never receive this server-only ACL file.
    """
    if (
        type(password) is not bytes
        or not 1 <= len(password) <= 256
        or any(value <= 32 or value >= 127 for value in password)
    ):
        raise ConfigError("The Valkey credential has invalid bytes.")
    commands = (
        "+ping +evalsha +eval +script|load +script|exists +time "
        "+zremrangebyscore +zadd +expire +zremrangebyrank +zcard "
        "+hmget +hset +get +set +hincrby +hgetall +select +client|setinfo +info"
    )
    return (
        "user default off\nuser web on #"
        + hashlib.sha256(password).hexdigest()
        + " ~stewardship:auth:v1:* ~stewardship:ops:v1:* "
        + commands
        + "\n"
    ).encode("ascii")
