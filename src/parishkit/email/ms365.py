"""MS365 email provider placeholder."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from parishkit.config import ConfigError
from parishkit.email.base import Email, EmailMessage, EmailProvider, build_message


@dataclass(frozen=True)
class MS365Provider(EmailProvider):
    tenant_id: str | None = None
    client_id: str | None = None

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> MS365Provider:
        return cls(
            tenant_id=config.get("tenant_id")
            if isinstance(config.get("tenant_id"), str)
            else None,
            client_id=config.get("client_id")
            if isinstance(config.get("client_id"), str)
            else None,
        )

    def send(self, message: Email, *, dry_run: bool = False) -> EmailMessage:
        built = build_message(message)
        if dry_run:
            return built
        raise ConfigError(
            "MS365 email provider is not implemented yet; configure "
            "provider: google-workspace or add tenant_id/client_id for "
            "future MS365 support"
        )
