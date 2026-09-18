"""Read-only in-container proof of unattended Gunicorn-child observations."""

import sys
import time
from pathlib import Path

from parishkit.stewardship.deployment import load_deployment
from parishkit.stewardship.operator_commands import configure_operator_database

# This helper assembles only the actual web SQL identity from its existing
# mount. It does not call configure_web, authentication, or check_health: those
# would themselves produce a sample and mask a missing periodic worker hook.
configure_operator_database(load_deployment(Path(sys.argv[1])))

from django.db import connection, connections  # noqa: E402


def observed():
    """Inspect only non-identifying durable observation metadata."""
    with connection.cursor() as cursor:
        cursor.execute("SET statement_timeout='2s'")
        cursor.execute("SELECT max(observed_at) FROM stewardship_limiter_health")
        return cursor.fetchone()[0]


try:
    before = observed()
    assert before is not None
    # Startup phases are jittered by up to thirty seconds before the first
    # thirty-second interval. Later ticks normally advance this much sooner.
    deadline = time.monotonic() + 75
    while observed() <= before:
        assert time.monotonic() < deadline, "Periodic auth observation did not advance"
        time.sleep(0.5)
finally:
    connections.close_all()
print("PERIODIC_AUTH_OBSERVATION_OK")
