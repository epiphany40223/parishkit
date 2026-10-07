"""Live-status pages at their real addresses, for in-place Refresh and Dismiss.

#519 PR 7a: each page's Refresh link and an integration's Dismiss re-read
the page itself, so the page is served where its controls lead. A test
answers the re-read with another of these pages (a route), so the fresh
region differs; the integration's Dismiss is answered by the fixture server
with a real Post/Redirect/Get redirect (``POSTS``).
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts.integration_credentials import CredentialSummary
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.jobs.send_progress_views import (
    GIVE_UP_MILLISECONDS,
    POLL_MILLISECONDS,
    _announcement,
)

from .send_progress_components import _send
from .step_up_components import render

REQUEST, RUNNING = UUID(int=700), UUID(int=703)
CREDENTIAL = reverse("admin:credential_status", args=[REQUEST])
# A key change still being installed when its page opens, so its watcher
# is already running when Refresh replaces the region.
CREDENTIAL_RUNNING = reverse("admin:credential_status", args=[RUNNING])
PROGRESS = reverse("admin:family_email_progress")
# The passive status the progress page polls while a send runs.
PROGRESS_STATUS = "/live-progress-status"
FAMILY_TESTS = reverse(
    "admin:campaign_mail_families", args=[UUID(int=701), UUID(int=702)]
)
INTEGRATION = reverse("admin:integration_settings", args=["parishsoft"])
DISMISS = reverse("admin:dismiss_credential_result", args=["parishsoft"])
# The fixture server's answer to Dismiss (status, Location, body).
POSTS = {DISMISS: (303, INTEGRATION + "?dismissed=1", "")}


def components(context, admin):
    """Each page as first shown, and the fresh versions the tests swap in."""
    now = context["server_now"]
    page = context | {"admin_chrome": admin}

    def credential(state, pending, request=REQUEST):
        """The key replacement status page of ``request`` in ``state``."""
        return render_to_string(
            "stewardship/credential-status.html",
            page
            | {
                "receipt": {"request_id": request, "state": state},
                "pending": pending,
                "admin_chrome": admin
                | {"back": {"label": "ParishSoft", "url": INTEGRATION}},
            },
        )

    def progress(template="family-email-progress", **counts):
        """Family email progress for a running send with these counts, which
        the page watches through PROGRESS_STATUS."""
        send = _send(now, **counts)
        return render_to_string(
            f"stewardship/{template}.html",
            page
            | {
                "status_url": PROGRESS_STATUS,
                "campaign": SimpleNamespace(pk=UUID(int=413)),
                "testing": False,
                "paused": False,
                "send": send,
                "upcoming": False,
                "follow": True,
                "announcement": _announcement(send),
                "poll_interval": POLL_MILLISECONDS,
                "give_up": GIVE_UP_MILLISECONDS,
            },
        )

    def family_tests(items):
        """Send to chosen Families with these recent tests, none pending."""
        return render(
            "family-tests",
            context,
            admin,
            confirm=False,
            families=[],
            items=items,
            refresh=False,
            refresh_url=FAMILY_TESTS,
        )

    def integration(summary):
        """ParishSoft's settings with ``summary`` as its key-change status."""
        return render_to_string(
            "stewardship/integration-settings.html",
            page
            | {
                "target": "parishsoft",
                "label": "ParishSoft",
                "summary": summary,
                "credential": None,
                "form": IntegrationForm(
                    "parishsoft",
                    initial={"organization_id": 12345, "base_digest": "a" * 64},
                ),
            },
        )

    failed = CredentialSummary(
        "failed", now, "ParishSoft did not accept the new API key.", REQUEST
    )
    tested = [{"duid": 1234, "label": "Sent", "created_at": now - timedelta(minutes=1)}]
    pages = {
        CREDENTIAL: credential("failed", False),
        "/live-credential-pending": credential("awaiting_ack", True),
        "/live-credential-applied": credential("applied", False),
        CREDENTIAL_RUNNING: credential("awaiting_ack", True, RUNNING),
        "/live-running-pending": credential("awaiting_ack", True, RUNNING),
        "/live-running-applied": credential("applied", False, RUNNING),
        PROGRESS: progress(),
        "/live-progress-later": progress(sent=900, remaining=180),
        PROGRESS_STATUS: progress(
            "family-email-progress-status", sent=900, remaining=180
        ),
        FAMILY_TESTS: family_tests([]),
        "/live-family-tests-later": family_tests(tested),
        INTEGRATION: integration(failed),
        INTEGRATION + "?dismissed=1": integration(None),
    }
    return {path: ("text/html", body) for path, body in pages.items()}
