"""The SECURITY DEFINER functions that may write tasks are a reviewed list (#350).

The task login guard (``stewardship_task_login_guard_v1``) binds every task
claim and transition to the login that executes the task type, but exempts
SECURITY DEFINER bodies: they run as the schema owner. Each such body is a
path around the per-login binding, so a new one must be a deliberate, reviewed
addition to this list rather than something the guard silently allows.
"""

import re
from pathlib import Path

SCHEMA = Path(__file__).parents[2] / "src/parishkit/stewardship/schema"
FUNCTION = re.compile(
    r"CREATE (?:OR REPLACE )?FUNCTION\s+(?:public\.)?(\w+)\((.*?)\$(\w*)\$(.*?)\$\3\$",
    re.S,
)
TASK_WRITE = re.compile(
    r"\b(?:INSERT INTO|UPDATE)\s+(?:public\.)?stewardship_task_run\b"
)
# Every owner-privileged task writer, and why it may bypass the login binding.
ALLOWED = {
    # An Admin's post-close "cancel" decision cancels waiting delivery tasks.
    "stewardship_delivery_resolve_closed_v1",
    # Cancel waiting delivery tasks that recovery replaces.
    "stewardship_delivery_recover_families_v1",
    "stewardship_delivery_recover_digests_v1",
    # Confirming Production queues the activation catch-up task.
    "stewardship_production_confirmation_effect_v1",
    # Cancels waiting schedule work that a replaced schedule supersedes.
    "stewardship_schedule_cancel_v1",
}


def _definer_task_writers():
    """Names of SECURITY DEFINER functions whose body writes stewardship_task_run."""
    found = set()
    for path in sorted(SCHEMA.glob("*.sql")):
        for name, header, _, body in FUNCTION.findall(path.read_text()):
            if "SECURITY DEFINER" in header and TASK_WRITE.search(body):
                found.add(name)
    return found


def test_security_definer_task_writers_are_the_reviewed_list():
    """A new owner-privileged task writer needs review and an entry here."""
    assert _definer_task_writers() == ALLOWED
