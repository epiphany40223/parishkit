"""Credential-free pure/HTTP tests; database integration is a separate profile."""

from .base import *  # noqa: F403

SECRET_KEY = "test-scaffold-only-not-a-production-secret"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]"]
STEWARDSHIP_DEPLOYMENT_PROFILE = "test"
# A WARNING-or-above log entry without its required context fails the test
# that wrote it (audit.log_contract, #633); production logs the miss instead.
STEWARDSHIP_LOG_CONTRACT_STRICT = True
# An Admin form error without a closed code fails the test that raised it
# (web.admin_forms, #1437): the refusal log records codes, never messages.
STEWARDSHIP_ERROR_CODES_STRICT = True
