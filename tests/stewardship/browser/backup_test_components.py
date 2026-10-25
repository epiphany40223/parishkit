"""Off-site backups' Test access, in place (#519).

The settings page is served at its real address, and its status line polls
the real status address. The fixture server answers Test access's POST as its
view does: a Post/Redirect/Get redirect back to the page, here with a query
(``TESTED``) whose page shows the check running. The status address answers
with the settled check, whose quiet follow-up re-reads ``SETTLED``, the page
showing that result. Pages at two other queries (``REFUSED``,
``REFUSED_LINKED``) have their Test access refused as the view refuses a
script's fetch: a typed error's JSON (400), never a page (#519). Two more
(``DENIED_SIGNED_IN``, ``DENIED_SIGNED_OUT``) have it refused with the typed
``denied`` 403, as the browser-error middleware answers a script for a
session that is still live and for one refused before admission (#2172).
"""

from types import SimpleNamespace
from unittest import mock
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import RequestFactory
from django.urls import reverse

from parishkit.stewardship.accounts import integration_views
from parishkit.stewardship.accounts.admin_editing import error_response
from parishkit.stewardship.accounts.integration_forms import IntegrationForm
from parishkit.stewardship.accounts.policy import Principal
from parishkit.stewardship.web import error_pages
from parishkit.stewardship.web.refusals import UserFacingError

PAGE = reverse("admin:integration_settings", args=["backup"])
STATUS = reverse("admin:integration_status", args=["backup"])
TESTED = f"{PAGE}?tested=1"
SETTLED = f"{PAGE}?settled=1"
REFUSED = f"{PAGE}?refused=1"
REFUSED_LINKED = f"{PAGE}?refused=linked"
DENIED_SIGNED_IN = f"{PAGE}?refused=signed-in"
DENIED_SIGNED_OUT = f"{PAGE}?refused=signed-out"
# Test access refused at the folder link field: the view draws the page
# again with the field's error (400, #1330).
BAD_LINK = f"{PAGE}?bad=link"
BAD_FOLDER = "https://drive.google.com/drive/u/0/my-drive"
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

    def settings(probe=None, form=None, url=PAGE):
        """The settings page at ``url`` with ``probe`` as the reader's latest
        check, and ``form`` (a refused entry) in place of the saved settings;
        its forms post to ``url`` itself, as the view's do."""
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
                # Save and Test access post to the page itself (#750).
                "page_url": url,
                "refresh_url": url,
                "form": form
                or IntegrationForm(
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
        REFUSED: settings(url=REFUSED),
        REFUSED_LINKED: settings(url=REFUSED_LINKED),
        DENIED_SIGNED_IN: settings(url=DENIED_SIGNED_IN),
        DENIED_SIGNED_OUT: settings(url=DENIED_SIGNED_OUT),
        BAD_LINK: settings(url=BAD_LINK),
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
        DENIED_SIGNED_IN: denied(DENIED_SIGNED_IN, live=True),
        DENIED_SIGNED_OUT: denied(DENIED_SIGNED_OUT, live=False),
        BAD_LINK: (400, None, bad_link(context, admin), "text/html"),
    }


def denied(path, *, live):
    """The middleware's answer to a fetch the view refused as ``denied``.

    The view's plain ``PermissionError`` passes through the real
    browser-error middleware. With ``live`` the request was admitted and the
    middleware's fresh read-only session check (stubbed here, as it needs
    the database) still admits it; otherwise the request was refused before
    any admission and no check runs (#2172).
    """
    request = RequestFactory().post(path, HTTP_X_REQUESTED_WITH="fetch")
    principal = Principal(identity=uuid4(), roles=frozenset({"administrator"}))
    if live:
        request.principal = principal
        request.session = SimpleNamespace(session_key="key")
    with mock.patch.object(error_pages, "_read_only_admission", lambda _: principal):
        response = error_pages.BrowserErrorMiddleware(
            lambda _: error_response(PermissionError("refused"))
        )(request)
    return (403, None, response.content.decode(), "application/json")


def bad_link(context, admin):
    """The page the view draws when Test access's folder link is refused."""
    form = IntegrationForm(
        "backup", {"base_digest": "a" * 64, "target": BAD_FOLDER, "action": "test"}
    )
    assert not form.is_valid() and form.has_error("target")
    return render_to_string(
        "stewardship/integration-settings.html",
        context
        | {
            "admin_chrome": admin,
            "complete_gate": True,
            "target": "backup",
            "label": "Off-site backups (Google Drive)",
            "configured": True,
            "workspace_configured": True,
            "status_url": STATUS,
            # Save and Test access post to the page itself (#750).
            "page_url": BAD_LINK,
            "refresh_url": BAD_LINK,
            "form": form,
            # The error summary the view draws on a 400, linking the field.
            "errors": integration_views._summary(form),
        },
    )
