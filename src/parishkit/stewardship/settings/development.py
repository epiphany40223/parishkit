"""Loopback-only scaffold without real credentials, mail, or persistence."""

from .base import *  # noqa: F403

DEBUG = True
SECRET_KEY = "development-scaffold-only-not-a-production-secret"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
