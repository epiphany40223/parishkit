"""Off-site backups' Test access, in place (#519).

The settings page is served at its real address, and its status line polls
the real status address. The fixture server answers Test access's POST as its
view does: a Post/Redirect/Get redirect back to the page, here with a query
(``TESTED``) whose page shows the check running. The status address answers
with the settled check, whose quiet follow-up re-reads ``SETTLED``, the page
showing that result. Pages at two other queries (``REFUSED``,
``REFUSED_LINKED``) have their Test access refused as the view refuses a
script's fetch: a typed error's JSON (400), never a page (#519).
"""

from types import SimpleNamespace
from unittest import mock

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import integration_views
from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.web.refusals import UserFacingError

PAGE = reverse("admin:integration_settings", args=["backup"])
STATUS = reverse("admin:integration_status", args=["backup"])
TESTED = f"{PAGE}?tested=1"
SETTLED = f"{PAGE}?settled=1"
REFUSED = f"{PAGE}?refused=1"
REFUSED_LINKED = f"{PAGE}?refused=linked"
FOLDER = "https://drive.google.com/drive/folders/synthetic-folder"


def components(context, admin):
    """The page before a check, while it runs and once it passed, the
    status address's settled line, and the page whose check is refused."""
    now = context["server_now"]
    values = context | {"admin_chrome": admin, "complete_gate": True}
    offsite = {"kind": "uploaded", "message": "Last off-site copy saved.", "at": now}
    pending = SimpleNamespace(
        kind="pending", message="Checking access to the folder…", folder_url=FOLDER
    )
    passed = SimpleNamespace(kind="passed", message="Access works.", folder_url=FOLDER)

    def settings(probe=None):
        """The settings page with ``probe`` as the reader's latest check."""
        return render_to_string(
            "stewardship/integration-settings.html",
            values
            | {
                "target": "backup",
                "label": "Off-site backups (Google Drive)",
                "configured": True,
                "workspace_configured": True,
                "offsite": offsite,
                "probe": probe,
                "pending": probe is pending,
                "status_url": STATUS,
                "form": IntegrationForm(
                    "backup", initial={"base_digest": "a" * 64, "target": FOLDER}
                ),
            },
        )

    status = render_to_string(
        "stewardship/backup-status.html",
        {"offsite": offsite, "probe": passed, "pending": False, "follow_url": SETTLED},
    )
    responses = {
        PAGE: settings(),
        TESTED: settings(pending),
        SETTLED: settings(passed),
        REFUSED: settings(),
        REFUSED_LINKED: settings(),
        STATUS: status,
    }
    return {path: ("text/html", html) for path, html in responses.items()}


def posts(context, admin):
    """The fixture server's answers: Test access's redirect back to the
    page, and the two refused pages' typed errors, answered as the real
    view answers a script's fetch: JSON (400) from ``error_response``, never
    a page. ``REFUSED`` is the view's own LOCAL refusal (no link);
    ``REFUSED_LINKED`` one that names where to fix it."""
    try:
        with mock.patch.object(integration_views, "_drive_in_local", lambda: True):
            integration_views._refuse_drive_in_local()
    except UserFacingError as error:
        local = error
    linked = UserFacingError(
        "Test access needs Google Workspace mail to be set up first.",
        fix="Set up Google Workspace mail, then come back and choose Test access.",
        link=reverse("admin:integration_settings", args=["google_workspace"]),
        link_label="Google Workspace mail",
    )

    def refused(error):
        """The view's JSON answer to a fetch refused with ``error``."""
        return (400, None, error_response(error).content.decode(), "application/json")

    return {
        PAGE: (303, TESTED, ""),
        REFUSED: refused(local),
        REFUSED_LINKED: refused(linked),
    }
