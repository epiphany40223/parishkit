"""Credential-free pure/HTTP tests; database integration is a separate profile."""

from .base import *  # noqa: F403

SECRET_KEY = "test-scaffold-only-not-a-production-secret"
ALLOWED_HOSTS = ["testserver", "localhost", "127.0.0.1", "[::1]"]
