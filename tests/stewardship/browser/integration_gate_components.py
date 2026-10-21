"""Integration and backup key pages as drawn before an entry is complete (#563).

Slack not set up (its token required), off-site backups before Google
Workspace mail is set up (Test access unavailable with why) and both steps of
a backup key change, each with the real templates and forms. Share options is
served by conftest's own /share-settings page. Set-up Slack and off-site
backups, with Save waiting for a change, and Slack not set up while its key
can't be stored, are #1330's.
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
        # Slack set up: Save waits for a change (#1330); the key is optional.
        "/slack-set-up": (
            "integration-settings",
            {
                "target": "slack",
                "label": "Slack notifications",
                "configured": True,
                "change_gate": True,
                "form": IntegrationForm(
                    "slack",
                    initial={"base_digest": "a" * 64, "channel_id": "C0123456789"},
                ),
                "credential": InlineCredentialForm(
                    "slack", initial={"intent": "synthetic-intent"}
                ),
            },
        ),
        # Slack not set up while its key can't be stored: Save is unavailable.
        "/slack-key-unavailable": (
            "integration-settings",
            {
                "target": "slack",
                "label": "Slack notifications",
                "configured": False,
                "change_gate": True,
                "credential_unavailable": True,
                "form": IntegrationForm("slack", **form),
            },
        ),
        # Off-site backups set up: Save waits for a change, Test access never.
        "/backup-set-up": (
            "integration-settings",
            {
                "target": "backup",
                "label": "Off-site backups (Google Drive)",
                "configured": True,
                "workspace_configured": True,
                "change_gate": True,
                "form": IntegrationForm(
                    "backup",
                    initial={
                        "base_digest": "a" * 64,
                        "target": "https://drive.google.com/drive/folders/" + "a" * 20,
                    },
                ),
            },
        ),
        # The first step of a key change, with nothing pasted yet.
        "/backup-key-new": (
            "backup-key",
            {
                "label": "Backup encryption key",
                "page_url": "/admin/system/integrations/backup_key/",
                "current": KeyStatus("configured", "0123456789abcdef"),
            },
        ),
        # The second step, waiting for the code.
        "/backup-key-code": (
            "backup-key",
            {
                "label": "Backup encryption key",
                "page_url": "/admin/system/integrations/backup_key/",
                "current": KeyStatus("configured", "0123456789abcdef"),
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
