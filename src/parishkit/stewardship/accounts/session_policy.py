"""Pure session deadlines shared by authentication and setup lifetime policy.

SQL guards freeze these intervals independently. Changing them also requires a
guard migration and the installed-policy contract tests, not just a Python edit.
``tests/stewardship/test_session_idle_parity.py`` checks that every SQL idle
literal still equals the idle limit here for its session table.
"""

from datetime import timedelta

ADMIN_IDLE = timedelta(minutes=60)
ADMIN_ABSOLUTE = timedelta(hours=12)
FAMILY_IDLE = timedelta(minutes=60)
FAMILY_ABSOLUTE = timedelta(hours=4)
