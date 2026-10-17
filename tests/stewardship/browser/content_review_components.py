"""Pages and emails' editor reviewed in the review dialog (#750, Slice C).

Every page is the real editor template (``content-settings.html``); every
answer is the real review fragment (``content-review.html``) or the real
editor drawn again, so the browser checks need no database.

``EDITOR`` is a saved Reminder email's editor at version ``"a" * 64``,
posting to itself, with Remove available and the campaign banner box; its
dialog's Apply returns to Pages and emails (``content_catalog_components``'
``CATALOG``). A test answers Preview changes with ``REVIEW`` (the full
review: a fictional sample of the current and proposed email and the
schedule the new text switches), ``REFUSED`` (the editor drawn again with
an HTML source error, status 400) or ``UNCHANGED`` (the server's refusal of
a Preview that changes nothing, for the dialog). Apply is redirected by the
fixture server (``POSTS``) to the change's status page, Applied.
"""

import json
from datetime import UTC, datetime
from uuid import uuid4

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.web.content import sanitize_html

from .content_catalog_components import APPLIED, CATALOG

EDITOR = "/content-review-editor"
REVIEW = "/content-review-answer"
REFUSED = "/content-review-refused"
UNCHANGED = "/content-review-unchanged"
POSTS = {EDITOR: (303, APPLIED, "")}
SAVED = "<p>Please respond by Friday.</p>"
# The schedule the review lists, at an instant the browser shows locally.
DUE = datetime(2054, 10, 15, 13, 0, tzinfo=UTC)


def components(context, admin):
    """Every page and answer above, keyed by the path it is fetched at."""

    def editor(data=None, *, status_errors=False):
        """The Reminder editor showing ``SAVED``, or bound to ``data``."""
        form = ContentForm(
            data,
            kind="email",
            slot="reminder",
            banner=True,
            initial={
                "base_digest": "a" * 64,
                "subject": "Please respond",
                "html": SAVED,
                "generate_text": True,
            },
        )
        if data:
            form.is_valid()
        return (
            "text/html",
            render_to_string(
                "stewardship/content-settings.html",
                context
                | {
                    "admin_chrome": admin,
                    "campaign": {
                        "pk": uuid4(),
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "label": "Reminder",
                    "breadcrumb_label": "Reminder",
                    "visual": sanitize_html(form["html"].value() or ""),
                    "placeholders": ["family_name", "parish_name"],
                    "form": form,
                    "change_gate": not data,
                    "post_url": EDITOR,
                    "catalog_url": CATALOG,
                    "errors": summary_errors(form) if status_errors else [],
                },
            ),
        )

    sample = {"subject": "Please respond", "html": SAVED, "text": "Please respond"}
    return {
        EDITOR: editor(),
        REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/content-review.html",
                {
                    "label": "Reminder",
                    "before": sample,
                    "after": sample | {"html": "<p>Please respond by Monday.</p>"},
                    "affected": [
                        {
                            "label": "Reminder",
                            "due_at": DUE,
                            "values": {"kind": "reminder", "subject": "Please respond"},
                        }
                    ],
                    "preview": "synthetic-signed-preview",
                },
            ),
        ),
        REFUSED: editor(
            {
                "base_digest": "a" * 64,
                "action": "preview",
                "subject": "Please respond",
                "html": "<p>{{ unknown_name }}</p>",
                "generate_text": "on",
            },
            status_errors=True,
        ),
        UNCHANGED: (
            "application/json",
            json.dumps(
                {
                    "errors": [{"code": "invalid", "field_id": None}],
                    "refusal": {
                        "message": "No content has changed.",
                        "fix": "Nothing was changed.",
                        "link": None,
                    },
                }
            ),
        ),
    }
