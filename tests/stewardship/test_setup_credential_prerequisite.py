"""Which setup credential steps report a blocking prerequisite, and which do not."""

from parishkit.stewardship.accounts.setup_credential_views import prerequisite
from parishkit.stewardship.accounts.setup_wizard import Step


class Wizard:
    """A stepper holding one credential step in a chosen state."""

    def __init__(self, state, status="Not done yet", fix_url=None):
        self.only = Step(
            number=5,
            key="google_workspace",
            label="Google Workspace mail",
            href="/admin/setup/credentials/google_workspace",
            url=None,
            state=state,
            status=status,
            current=True,
            fix_url=fix_url,
        )

    def step(self, key):
        """Return the one step when asked for its key, else None."""
        return self.only if key == self.only.key else None


def test_a_blocked_step_reports_its_status_and_fix_page():
    """The generic blocked branch returns the step's own status and fix link."""
    status = "Not available yet: Save the outgoing email settings first."
    wizard = Wizard("blocked", status=status, fix_url="/admin/setup/mail")
    assert prerequisite(wizard, "google_workspace") == (status, "/admin/setup/mail")


def test_only_a_blocked_step_is_a_prerequisite():
    """Ordering-only locks, unfinished and done steps have nothing to fix first."""
    for state in ("locked", "todo", "done"):
        assert prerequisite(Wizard(state), "google_workspace") is None
    assert prerequisite(None, "google_workspace") is None
