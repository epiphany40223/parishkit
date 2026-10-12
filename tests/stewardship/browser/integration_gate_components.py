"""Integration and backup key pages as drawn before an entry is complete (#563).

Slack not set up (its token required), off-site backups before Google
Workspace mail is set up (Test access unavailable with why) and both steps of
a backup key change, each with the real templates and forms. Share options is
served by conftest's own /share-settings page.
"""

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.backup_key import KeyStatus
from parishkit.stewardship.accounts.integration_forms import (
    InlineCredentialForm,
    IntegrationForm,
)


def components(context, admin):
    """Serve each page from the real template with synthetic values."""
    values = context | {"admin_chrome": admin, "complete_gate": True}
    form = {"initial": {"base_digest": "a" * 64}}
    pages = {
        # Slack before it is set up: the channel and the token are required.
        "/slack-not-set-up": (
            "integration-settings",
            {
                "target": "slack",
                "label": "Slack notifications",
                "configured": False,
                "form": IntegrationForm("slack", **form),
                "credential": InlineCredentialForm(
                    "slack", initial={"intent": "synthetic-intent"}, required=True
                ),
            },
        ),
        # Off-site backups with no Google Workspace mail to test as.
        "/backup-no-workspace": (
            "integration-settings",
            {
                "target": "backup",
                "label": "Off-site backups (Google Drive)",
                "configured": False,
                "workspace_configured": False,
                "form": IntegrationForm("backup", **form),
            },
        ),
        # The first step of a key change, with nothing pasted yet.
        "/backup-key-new": (
            "backup-key",
            {
                "label": "Backup encryption key",
                "current": KeyStatus("configured", "0123456789abcdef"),
                "fresh": True,
            },
        ),
        # The second step, waiting for the code.
        "/backup-key-code": (
            "backup-key",
            {
                "label": "Backup encryption key",
                "current": KeyStatus("configured", "0123456789abcdef"),
                "fresh": True,
                "new_fingerprint": "fedcba9876543210",
                "challenge": "PKBKP1:" + "A" * 80,
                "intent": "synthetic-intent",
            },
        ),
    }
    return {
        path: (
            "text/html",
            render_to_string(f"stewardship/{template}.html", values | extra),
        )
        for path, (template, extra) in pages.items()
    }
