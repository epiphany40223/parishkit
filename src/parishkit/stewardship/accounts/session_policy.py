"""Pure session deadlines shared by authentication and setup lifetime policy.

SQL guards freeze these intervals independently. Changing them also requires a
guard migration and the installed-policy contract tests, not just a Python edit.
"""

from datetime import timedelta

ADMIN_IDLE = timedelta(minutes=30)
ADMIN_ABSOLUTE = timedelta(hours=12)
FAMILY_IDLE = timedelta(minutes=60)
FAMILY_ABSOLUTE = timedelta(hours=4)
