"""Fail closed until production configuration and admission gates are wired."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

# A fixed developer key or an ad hoc environment flag must never make this
# incomplete scaffold production-runnable. ARC-02 owns validated deployment
# settings; later admission packages own safe setup and campaign availability.
raise ImproperlyConfigured(
    "Production startup is unavailable: Stewardship deployment validation "
    "has not been implemented (ARC-02)."
)
