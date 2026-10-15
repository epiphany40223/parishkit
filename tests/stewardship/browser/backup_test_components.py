"""Off-site backups' Test access, in place (#519).

The settings page is served at its real address, and its status line polls
the real status address. The fixture server answers Test access's POST as its
view does: a Post/Redirect/Get redirect back to the page, here with a query
(``TESTED``) whose page shows the check running. The status address answers
with the settled check, whose quiet follow-up re-reads ``SETTLED``, the page
showing that result. A page at another query (``REFUSED``) has its Test
access refused with the view's fixed error page (400).
"""

from types import SimpleNamespace

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.integration_forms import IntegrationForm

PAGE = reverse("admin:integration_settings", args=["backup"])
STATUS = reverse("admin:integration_status", args=["backup"])
TESTED = f"{PAGE}?tested=1"
SETTLED = f"{PAGE}?settled=1"
REFUSED = f"{PAGE}?refused=1"
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
        STATUS: status,
    }
    return {path: ("text/html", html) for path, html in responses.items()}


def posts(context, admin):
    """The fixture server's answers: Test access's redirect back to the
    page, and the refused page's error page."""
    refused = render_to_string(
        "stewardship/error.html",
        context
        | {
            "admin_chrome": admin,
            "admin": True,
            "title": "Enter the Google Drive folder link first.",
            "error_messages": ["Paste the folder's link, then choose Test access."],
            "home": "/admin/",
            "home_label": "Administration home",
        },
    )
    return {PAGE: (303, TESTED, ""), REFUSED: (400, None, refused)}
