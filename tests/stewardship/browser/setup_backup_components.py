"""The setup wizard's optional Off-site backups step (#2200).

Drawn from the real template and form, just before Finish setup:

- ``STEP``: the step at its real address on a first visit, with the email
  test accepted and every required step done, so Finish setup is open.
- ``SAVED``: the step revisited after it was saved on.
- ``LOCAL``: the step in a local environment that does not reach Google
  Drive, its switch unavailable with the reason.

Each page's form posts to its own address, answered (``POSTS``) as the real
view answers a valid save: a redirect to the next step not done yet, here
Finish setup (``FINISH``, drawn ready).
"""

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.setup_forms import (
    BACKUP_MISSING,
    SetupBackupForm,
    initial_values,
)
from parishkit.stewardship.accounts.setup_wizard import BY_KEY

from ..test_setup_final_steps import wizard as final_wizard
from .setup_order_components import FINISH_READY as FINISH

STEP = BY_KEY["backup"].url
SAVED = "/setup-backup-saved"
LOCAL = "/setup-backup-local"
MAILBOX = "mail@example.org"
LINK = "https://drive.google.com/drive/folders/A1b2C3d4E5f6G7h8I9j0"
ON = {"enabled": True, "folder_link": LINK, "acknowledged": True}
POSTS = {path: (303, FINISH, "") for path in (STEP, SAVED, LOCAL)}


def components(context, admin, setup_draft):
    """The step on a first visit, revisited saved on, and closed in LOCAL."""
    tests = frozenset({"mail_test"})

    def step(values=None, *, drive_closed=False):
        """The step page with ``values`` saved, as ``setup_step`` draws it."""
        form = SetupBackupForm(
            initial=initial_values("backup", values or {}),
            mailbox=MAILBOX,
            drive_closed=drive_closed,
        )
        return (
            "text/html",
            render_to_string(
                "stewardship/setup-step.html",
                context
                | {
                    "admin_chrome": admin,
                    "draft": setup_draft,
                    "wizard": final_wizard("backup", tests=tests),
                    "form": form,
                    "step": "backup",
                    "step_label": BY_KEY["backup"].label,
                    "backup_on": bool((values or {}).get("enabled")),
                    "backup_mailbox": MAILBOX,
                    "backup_missing": BACKUP_MISSING,
                },
            ),
        )

    return {STEP: step(), SAVED: step(ON), LOCAL: step(drive_closed=True)}
