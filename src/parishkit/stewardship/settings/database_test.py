"""Explicit disposable PostgreSQL tests, never a production database profile.

Only a loopback server and the fixed test-only database/user names are accepted.
CI supplies an ephemeral PostgreSQL service; local developers use the documented
disposable container. Normal pure tests retain the dummy database backend.
"""

import os

from .test import *  # noqa: F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": "127.0.0.1",
        "PORT": int(os.environ.get("PARISHKIT_TEST_POSTGRES_PORT", "55432")),
        "NAME": "stewardship_tests",
        "USER": "stewardship_tests",
        "PASSWORD": "disposable-test-only",
        "TEST": {"NAME": "test_stewardship_tests"},
        "OPTIONS": {"connect_timeout": 5},
    }
}
