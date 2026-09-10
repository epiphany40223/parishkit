"""Only run database tests with the explicit disposable PostgreSQL profile."""

import pytest
from django.conf import settings

from .auth_builders import auth_service, google  # noqa: F401


def pytest_collection_modifyitems(items):
    """Skip before pytest-django can create a database in the pure baseline."""
    if settings.SETTINGS_MODULE != "parishkit.stewardship.settings.database_test":
        from pathlib import Path

        directory = Path(__file__).parent
        for item in items:
            if item.path.is_relative_to(directory):
                item.add_marker(
                    pytest.mark.skip(
                        reason="Requires disposable PostgreSQL database_test profile"
                    )
                )
