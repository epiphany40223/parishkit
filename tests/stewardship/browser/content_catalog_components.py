"""Pages and emails (#446) and its email template action tables (#879).

Rendered from the real template, so the browser checks need no database:

- ``CATALOG``: a Reminder table with an email two schedules send and two
  unused ones (one sharing the used one's subject), and the confirmation
  email. Its Delete posts to ``DELETE``, which answers as the server does: a
  redirect to the change's status page, here already Applied, after which the
  dialog redraws the email tables from the page's refresh address, which
  shows the first unused reminder gone.
- ``BULK``: the same page whose refresh address shows both unused reminders
  gone, which also drops the table's selection column and bulk bar.
- ``REFUSED`` and ``FAILED``: pages whose Delete is refused with the server's
  explanation, or recorded but not applied.
- ``BARE``: a page whose Delete gets a bare 400, with no refusal of its own
  (an unexpected field the server refused, #1401).

``POSTS`` lists the fixture server's answers to the pages' POSTs; it is
filled in by ``components``, before the server starts.
"""

import json
from uuid import uuid4

from django.template.loader import render_to_string

CATALOG = "/content-catalog"
BULK = "/content-catalog-bulk"
REFUSED = "/content-catalog-refused"
FAILED = "/content-catalog-failed"
BARE = "/content-catalog-bare"
DELETE = "/content-catalog/deletion"
APPLIED = "/content-change-applied"
NOT_APPLIED = "/content-change-failed"
# The refusal's own explanation, which the dialog shows.
REFUSAL = "This email is used by a mail schedule, so it can't be removed."
POSTS = {}
# The rows' revision IDs, which the Delete controls post.
USED, SPARE, OTHER = (str(uuid4()) for _ in range(3))


def _reminder(identifier, subject, short_id, *, used_by=(), state="custom"):
    """One Reminder table row, shaped as ``content_views.catalog_entries`` builds it."""
    return {
        "id": identifier,
        "subject": subject,
        "short_id": short_id,
        "name": f"{subject} ({short_id})" if short_id else subject,
        "excerpt": "Welcome to {{ parish_name }}.",
        "used_by": list(used_by),
        "deletable": not used_by,
        "state": state,
        "resave": None,
        "test_url": "/campaign-mail",
        "url": f"/content-edit/{subject.split()[0].lower()}-{identifier[:4]}",
    }


def _email(label, slot, revisions, *, scheduled=True, singleton=False):
    """One mail type's table, shaped as ``catalog_entries`` builds it."""
    return {
        "label": label,
        "form_id": f"email-{slot}-actions",
        "dialog_id": f"email-{slot}-delete",
        "heading_id": f"email-{slot}-title",
        "deletable": any(row["deletable"] for row in revisions),
        "singleton": singleton,
        "scheduled": scheduled,
        "url": "/content-edit",
        "revisions": revisions,
    }


def _page(values, reminders, *, refresh_url, delete_url=DELETE):
    """Pages and emails listing ``reminders`` and the confirmation email."""
    confirmation = {
        "subject": "Thank you",
        "short_id": None,
        "name": "Thank you",
        "excerpt": "Thank you for responding.",
        "used_by": [],
        "deletable": False,
        "state": "default",
        "resave": None,
        "test_url": None,
        "url": "/content-edit",
    }
    context = {
        "campaign": {
            "pk": uuid4(),
            "active_configuration": {"name": "Sample campaign"},
        },
        "pages": [{"label": "Welcome", "url": "/content-edit", "state": "custom"}],
        "emails": [
            _email("Reminder", "reminder", reminders),
            _email(
                "Confirmation email",
                "confirmation",
                [confirmation],
                scheduled=False,
                singleton=True,
            ),
        ],
        "base_digest": "a" * 64,
        "delete_url": delete_url,
        "refresh_url": refresh_url,
    }
    return (
        "text/html",
        render_to_string("stewardship/content-catalog.html", values | context),
    )


def _status(values, state, **receipt):
    """A configuration change's status page in ``state``."""
    return (
        "text/html",
        render_to_string(
            "stewardship/configuration-request.html",
            values | {"receipt": {"request_id": uuid4(), "state": state, **receipt}},
        ),
    )


def components(context, admin):
    """Every page above, and the POST answers (see the module docstring)."""
    values = context | {"admin_chrome": admin}
    used = _reminder(
        USED, "Please respond", "1a2b3c4d", used_by=["Reminder 1", "Reminder 2"]
    )
    spare = _reminder(SPARE, "Please respond", "5e6f7a8b", state="default")
    other = _reminder(OTHER, "Last call", None)
    rows = [used, spare, other]
    POSTS.update(
        {
            DELETE: (303, APPLIED, ""),
            f"{BULK}/deletion": (303, APPLIED, ""),
            f"{REFUSED}/deletion": (
                400,
                None,
                json.dumps(
                    {
                        "errors": [{"code": "invalid"}],
                        "refusal": {
                            "message": REFUSAL,
                            "fix": "Nothing was deleted.",
                            "link": None,
                        },
                    }
                ),
            ),
            f"{FAILED}/deletion": (303, NOT_APPLIED, ""),
            f"{BARE}/deletion": (
                400,
                None,
                json.dumps({"errors": [{"code": "invalid", "field_id": None}]}),
            ),
        }
    )
    return {
        CATALOG: _page(values, rows, refresh_url=f"{CATALOG}-one-deleted"),
        f"{CATALOG}-one-deleted": _page(
            values, [used, other], refresh_url=f"{CATALOG}-one-deleted"
        ),
        BULK: _page(
            values, rows, refresh_url=f"{BULK}-deleted", delete_url=f"{BULK}/deletion"
        ),
        f"{BULK}-deleted": _page(values, [used], refresh_url=f"{BULK}-deleted"),
        REFUSED: _page(
            values, rows, refresh_url=REFUSED, delete_url=f"{REFUSED}/deletion"
        ),
        FAILED: _page(
            values, rows, refresh_url=FAILED, delete_url=f"{FAILED}/deletion"
        ),
        BARE: _page(values, rows, refresh_url=BARE, delete_url=f"{BARE}/deletion"),
        APPLIED: _status(values, "applied"),
        NOT_APPLIED: _status(values, "failed", failure_code="stale_base"),
    }
