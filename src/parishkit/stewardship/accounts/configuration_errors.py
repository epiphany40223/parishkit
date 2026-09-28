"""Typed configuration holds distinguish retryable readiness from invalid intent."""

from parishkit.config import ConfigError


class ConfigurationReadinessUnavailable(ConfigError):
    """The candidate may be valid, but its owning prerequisite is still pending."""


class ConfigurationHistoryInvalid(ConfigError):
    """The candidate cannot be installed on this deployment's applied history.

    This is deterministic: retrying the same request can never succeed, so the
    installer records a visible failure instead of retrying it forever (#187).
    """
