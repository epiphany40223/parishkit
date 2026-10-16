"""The Reminder WorkGroup setting (#861), rendered from its real templates.

Each path shows one state of the page (what the newest refresh found), so
the browser checks need no database or ParishSoft. Save confirms in the
shared review dialog (#750, #1356): ``EDITOR`` is the page with no WorkGroup
set, at version ``"a" * 64``, posting to itself; a test answers its Review
with ``CONFIRM`` (the real confirmation of setting ``NAME``) or ``REFUSED``
(the page drawn again with the name's error, status 400). Its Apply is
redirected by the fixture server (``POSTS``) to ``APPLIED``, the change's
status page, Applied; the page's regions are then redrawn from ``SETTLED``,
the page with ``NAME`` saved at version ``"b" * 64``, awaiting the next
refresh.
"""

from types import SimpleNamespace as Value
from uuid import UUID

from django.template.loader import render_to_string

from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.reminder_workgroup_views import WorkGroupForm

NAME = "Active: Stewardship 2027"
DIGEST = "a" * 64
EDITOR = "/reminder-workgroup-off"
CONFIRM = "/reminder-workgroup-confirm"
REFUSED = "/reminder-workgroup-refused"
APPLIED = "/reminder-workgroup-applied"
SETTLED = "/reminder-workgroup-settled"
REQUEST = UUID("75000000-0000-4000-8000-000000000750")
POSTS = {EDITOR: (303, APPLIED, "")}
# The name the refused Review sent, and its error, as the form refuses it.
TOO_LONG = "x" * 300


def components(context, admin):
    """The page with the WorkGroup found, missing and unset, and Save's
    answers."""
    campaign = Value(
        pk=UUID(int=86), active_configuration=Value(name="Annual campaign")
    )
    values = context | {"admin_chrome": admin, "campaign": campaign}
    states = {
        "found": (NAME, {"name": NAME, "found": True, "family_duids": [1, 2]}),
        "missing": (NAME, {"name": NAME, "found": False, "family_duids": []}),
        "off": (None, None),
    }

    def page(current, evidence, digest=DIGEST, data=None):
        """The page for ``current`` at ``digest``; with ``data``, the page a
        refused Review draws again (status 400), with its errors' summary.
        Save and Apply post to ``EDITOR``; the page's regions are redrawn
        from ``SETTLED``."""
        form = WorkGroupForm(
            data, initial={"name": current or "", "base_digest": digest}
        )
        if data is not None:
            assert not form.is_valid()
        return (
            "text/html",
            render_to_string(
                "stewardship/reminder-workgroup.html",
                values
                | {
                    "form": form,
                    "current": current,
                    "evidence": evidence,
                    "page_url": EDITOR,
                    "refresh_url": SETTLED,
                    "errors": summary_errors(form) if data is not None else [],
                },
            ),
        )

    responses = {
        f"/reminder-workgroup-{state}": page(current, evidence)
        for state, (current, evidence) in states.items()
    }
    responses[CONFIRM] = (
        "text/html",
        render_to_string(
            "stewardship/reminder-workgroup-review.html",
            {"review": {"clears": False, "preview": "synthetic"}},
        ),
    )
    responses[REFUSED] = page(
        None, None, data={"name": TOO_LONG, "base_digest": DIGEST}
    )
    responses[APPLIED] = (
        "text/html",
        render_to_string(
            "stewardship/configuration-request.html",
            context
            | {
                "admin_chrome": admin,
                "receipt": Value(state="applied", request_id=REQUEST, failure_code=""),
            },
        ),
    )
    responses[SETTLED] = page(NAME, None, digest="b" * 64)
    return responses
