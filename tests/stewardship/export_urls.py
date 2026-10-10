"""The shared export page's actions, reversed from the page's address (NAV-12).

Tests follow an export's ``Location`` to its page; this names the action
under that page without spelling its path, so the URL scheme stays the one
place the address is written.
"""

from django.urls import resolve, reverse

ACTIONS = {
    "cancel": "report_export_cancel",
    "retry": "report_export_retry",
    "download": "report_export_download",
    "regenerate": "report_export_regenerate",
}


def export_action(page, action):
    """The address of ``action`` on the export page at ``page``."""
    request_id = resolve(page.split("?")[0]).kwargs["request_id"]
    return reverse(f"admin:{ACTIONS[action]}", args=[request_id])
