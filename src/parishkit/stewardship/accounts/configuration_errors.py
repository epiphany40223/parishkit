"""Typed configuration holds distinguish retryable readiness from invalid intent."""

from parishkit.config import ConfigError


class ConfigurationReadinessUnavailable(ConfigError):
    """The candidate may be valid, but its owning prerequisite is still pending."""
