"""MS365 email provider placeholder."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from parishkit.config import ConfigError
from parishkit.email.base import Email, EmailMessage, EmailProvider, build_message


@dataclass(frozen=True)
class MS365Provider(EmailProvider):
    """Placeholder provider for future Microsoft 365 email support.

    The configuration shape (``tenant_id``/``client_id``) is accepted now so
    deployments can be set up ahead of implementation, but sending is not yet
    wired up; :meth:`send` only works in dry-run mode.
    """

    tenant_id: str | None = None
    client_id: str | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> MS365Provider:
        """Build the provider from YAML configuration.

        ``tenant_id`` and ``client_id`` are accepted only when present and
        string-typed; anything else is ignored and left as ``None``.
        """
        return cls(
            tenant_id=config.get("tenant_id")
            if isinstance(config.get("tenant_id"), str)
            else None,
            client_id=config.get("client_id")
            if isinstance(config.get("client_id"), str)
            else None,
        )

    def send(self, message: Email, *, dry_run: bool = False) -> EmailMessage:
        """Build the message; only dry-run is supported until MS365 lands.

        In dry-run mode the constructed message is returned for inspection.
        Any real send raises :class:`ConfigError` because the MS365 backend is
        not yet implemented.
        """
        built = build_message(message)
        if dry_run:
            return built
        raise ConfigError(
            "MS365 email provider is not implemented yet; configure "
            "provider: google-workspace or add tenant_id/client_id for "
            "future MS365 support"
        )
