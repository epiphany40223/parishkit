"""Refuse standalone settings imports that bypass operational admission."""

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F403

# A fixed developer key or an ad hoc environment flag must never make this
# settings module production-runnable. Runtime assembly owns validated deployment
# settings and enforces mount, credential, SQL and configuration admission.
raise ImproperlyConfigured(
    "Standalone production settings are unavailable: use the admitted "
    "pk-stewardship runtime entry point with explicit deployment configuration."
)
