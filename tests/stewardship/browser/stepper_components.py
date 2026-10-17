"""The Campaign setup stepper on a campaign page (#142 slice 4).

``PATH`` is Share options for a Financial stewardship Testing draft, drawn
with the stepper the chrome gives it: Campaign settings completed (by
creating the campaign), Share options current. Its Apply follows the real
review dialog flow (``settings_components``' Share options pages, #750):
``REVIEW`` is the Review's dialog fragment, ``POSTS`` answers Apply with
``APPLIED`` (Change status, applied, offering Continue), and the dialog then
redraws the page's regions from ``SETTLED``: the page again, with Share
options completed, so the stepper's Next reads Continue to the next step.

Continue (#1404) posts to its real address, answered with a redirect to
``CONTENT``, Pages and emails with that step current;
``EDITOR`` is its editor, a sub-page whose action row is Return to Pages and
emails.

``DIRECT`` is the same Share options as the campaign wizard draws it
(#1404 PR 4): no Save of its own, the action row's Save and continue saving
the form directly. Its save posts to ``DIRECT`` itself, answered with
``APPLIED``; its next step is ``CONTENT``, where the saved status line shows.
``DIRECT_RETRY`` is the same page whose save is answered with
``campaign_settings_components.NOT_APPLIED`` (a change the installer
refused), so the page stays. ``DIRECT_SETTINGS`` is Campaign settings as the
wizard draws it, Financial stewardship on, saving to itself (answered with
``APPLIED``) and going on to ``CONTENT``; ``DIRECT_SETTINGS_REFUSED`` is that
page drawn again with the campaign name's error, which a test answers a save
with (status 400).

The sub-pages that save directly (#1404 PR 4b): ``EDITOR_DIRECT`` is the
editor as the wizard draws it, saving to itself (answered with ``APPLIED``)
and going back to ``CONTENT``; ``EDITOR_DIRECT_REFUSED`` is that editor drawn
again with its HTML refused for an unknown placeholder, and its summary (a
test answers a save with it, status 400). ``NEW_DIRECT`` and ``EDIT_DIRECT``
are New and Edit scheduled email as the wizard draws them, saving to
themselves (``APPLIED``) and going back to ``SCHEDULES``, Dates and mail
schedules with its stepper.

``SUGGESTED`` is Dates and mail schedules as the wizard draws it for a
campaign with no schedule saved (#1404 PR 6a): the suggested schedules,
"Not saved yet", whose Add these schedules and continue posts the page's
version and request key to ``SUGGESTED`` itself (answered with ``APPLIED``)
and goes on to ``CONTENT``. ``SUGGESTED_PASSED`` is the same page once the
Initial invitation's send time has passed: nothing is suggested, so the
panel and Add are left out and only Continue without schedules for now
remains.
"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard, schedule_suggestions
from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.accounts.option_editing import option_changes
from parishkit.stewardship.accounts.schedule_forms import email_names
from parishkit.stewardship.accounts.schedule_table import SORTING
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.web.tables import whole_table

from . import schedule_list_components as schedule_pages
from .campaign_settings_components import NOT_APPLIED, VALUES, _form

PATH = "/campaign-stepper"
REVIEW = "/campaign-stepper-review"
APPLIED = "/campaign-stepper-applied"
SETTLED = "/campaign-stepper-settled"
REQUEST = UUID("14200000-0000-4000-8000-000000000142")
TEST_URL = "/admin/campaign/content/test/00000000-0000-0000-0000-000000001522/"
# Not Pages and emails' real address, which other component tests follow
# from the menu to a page of their own.
CONTENT = "/campaign-stepper-content"
EDITOR = "/campaign-stepper-editor"
DIRECT = "/campaign-stepper-direct"
DIRECT_RETRY = "/campaign-stepper-direct-retry"
DIRECT_SETTINGS = "/campaign-stepper-direct-settings"
DIRECT_SETTINGS_REFUSED = "/campaign-stepper-direct-settings-refused"
EDITOR_DIRECT = "/campaign-stepper-editor-direct"
EDITOR_DIRECT_REFUSED = "/campaign-stepper-editor-direct-refused"
SCHEDULES = "/campaign-stepper-schedules"
NEW_DIRECT = "/campaign-stepper-new-direct"
EDIT_DIRECT = "/campaign-stepper-edit-direct"
SUGGESTED = "/campaign-stepper-suggested"
SUGGESTED_PASSED = "/campaign-stepper-suggested-passed"
# Campaign settings with Financial stewardship on, complete.
FINANCIAL_VALUES = VALUES | {
    "financial_enabled": True,
    "financial_start": "2055-01-01",
    "financial_end": "2055-12-31",
    "comparison_start": "2054-01-01",
    "comparison_end": "2054-12-31",
    "fund_duids": ["12"],
    "comparison_fund_duids": ["12"],
}
POSTS = {
    PATH: (303, APPLIED, ""),
    DIRECT: (303, APPLIED, ""),
    DIRECT_RETRY: (303, NOT_APPLIED, ""),
    DIRECT_SETTINGS: (303, APPLIED, ""),
    EDITOR_DIRECT: (303, APPLIED, ""),
    NEW_DIRECT: (303, APPLIED, ""),
    EDIT_DIRECT: (303, APPLIED, ""),
    SUGGESTED: (303, APPLIED, ""),
    SUGGESTED_PASSED: (303, APPLIED, ""),
    reverse("admin:campaign_step_continue"): (303, CONTENT, ""),
}


def components(context, admin):
    """Share options with the Campaign setup stepper, through Apply."""
    options = default_share_options()
    renamed = [dict(options[0], label="Renamed option"), *options[1:]]

    def stepper(done, current="share_options", sub=False, direct=False, back=None):
        """The stepper on ``current`` with ``done`` completed.

        A ``direct`` one goes on to ``CONTENT``, the component Pages and
        emails, rather than the real address; a direct sub-page goes back to
        ``back``, its step's component page.
        """
        wizard = campaign_wizard.build(
            True, current, done=done, mail_test_url=TEST_URL, sub=sub, direct=direct
        )
        if direct and sub:
            steps = tuple(
                replace(step, url=back) if step.current else step
                for step in wizard.steps
            )
            wizard = replace(wizard, steps=steps)
        elif direct:
            wizard = replace(wizard, upcoming=replace(wizard.upcoming, url=CONTENT))
        return wizard

    def content(template, values, sub=False, direct=False):
        """A Pages and emails page (``sub``: its editor), Share options kept.

        A ``direct`` editor saves directly and goes back to ``CONTENT``.
        """
        chrome = admin | {
            "campaign_wizard": stepper(
                {"settings", "share_options"}, "content", sub, direct, back=CONTENT
            )
        }
        return (
            "text/html",
            render_to_string(
                f"stewardship/{template}",
                context
                | values
                | {
                    "admin_chrome": chrome,
                    "campaign": {
                        "pk": uuid4(),
                        "active_configuration": {"name": "Sample campaign"},
                    },
                },
            ),
        )

    def share(done, previous=options, digest="a" * 64, direct=False, page=None):
        """Share options with its stepper, saved as ``previous``.

        ``page`` is where the form posts (``DIRECT`` or ``PATH`` by default).
        """
        page = page or (DIRECT if direct else PATH)
        return (
            "text/html",
            render_to_string(
                "stewardship/share-settings.html",
                context
                | {
                    "admin_chrome": admin
                    | {"campaign_wizard": stepper(done, direct=direct)},
                    "campaign": {
                        "pk": uuid4(),
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "base_digest": digest,
                    "formset": ShareOptions(prefix="options", previous=previous),
                    "page_url": page,
                    "refresh_url": page if direct else SETTLED,
                },
            ),
        )

    def settings(form):
        """Campaign settings as the wizard draws it, saving to itself."""
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-settings.html",
                context
                | {
                    "admin_chrome": admin
                    | {"campaign_wizard": stepper(set(), "settings", direct=True)},
                    "campaign": {
                        "pk": uuid4(),
                        "state": "draft",
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "form": form,
                    "editable": True,
                    "open": True,
                    "page_url": DIRECT_SETTINGS,
                    "refresh_url": DIRECT_SETTINGS,
                    "errors": summary_errors(form) if form.is_bound else [],
                },
            ),
        )

    def editor(form, post_url):
        """The editor's values: its saved welcome page, or ``form``.

        Its change gate holds Save and go back while the saved page is shown
        (``content_views._page``'s ``change_gate``); a refused save drawn
        with its posts is saved as it is, so it is not gated.
        """
        return {
            "label": "Welcome page",
            "visual": "<p>Welcome</p>",
            "placeholders": ["family_name"],
            "form": form,
            "post_url": post_url,
            "change_gate": not form.is_bound,
            "errors": summary_errors(form) if form.is_bound else [],
        }

    welcome = {"base_digest": "a" * 64, "html": "<p>Welcome</p>"}
    # A placeholder the editor does not support: refused at its field.
    unknown = ContentForm(
        welcome
        | {"html": "<p>Welcome {{ unknown }}</p>", "generate_text": "on", "text": ""},
        kind="page",
        slot="welcome",
    )
    assert not unknown.is_valid()

    def schedule(values, path, schedule_row=None, current="schedules"):
        """New or Edit scheduled email inside the wizard, going back to
        ``SCHEDULES``; or (``current`` None) that list with its stepper."""
        done = {"settings", "share_options", "content"}
        chrome = admin | {
            "campaign_wizard": stepper(
                done, "schedules", sub=current is not None, direct=True, back=SCHEDULES
            )
            if current
            else stepper(done, "schedules")
        }
        owner, emails, rows = schedule_pages._data()
        if current is None:
            return schedule_pages._list(
                values | {"admin_chrome": chrome}, owner, emails, rows
            )
        return schedule_pages._entry(
            values | {"admin_chrome": chrome},
            owner,
            emails,
            rows,
            schedule_row=rows[1] if schedule_row else None,
            post_url=path,
        )

    def suggested(path=SUGGESTED, now=datetime(2054, 9, 1, tzinfo=UTC)):
        """Dates and mail schedules proposing its suggested schedules.

        No schedule is saved, so the wizard suggests (#1404 PR 6a); its Add
        these schedules and continue posts to ``SUGGESTED`` and goes on to
        ``CONTENT``. With ``now`` past the Initial invitation's send time
        nothing is suggested, as the wizard decides from the same rule.
        """
        owner, emails, _rows = schedule_pages._data()
        rows = schedule_suggestions.rows(
            owner["values"], emails, owner["id"], now, email_names(emails)
        )
        wizard = campaign_wizard.build(
            True,
            "schedules",
            done={"settings", "share_options", "content"},
            mail_test_url=TEST_URL,
            no_schedules=True,
            suggest=bool(rows),
        )
        wizard = replace(wizard, upcoming=replace(wizard.upcoming, url=CONTENT))
        return (
            "text/html",
            render_to_string(
                "stewardship/schedule-settings.html",
                context
                | {
                    "admin_chrome": admin | {"campaign_wizard": wizard},
                    "campaign": {
                        "pk": owner["id"],
                        "active_configuration": owner["values"],
                    },
                    "values": owner["values"],
                    "editable": True,
                    "base_digest": "a" * 64,
                    "table": whole_table([], sorting=SORTING, sort="when"),
                    "refresh_url": path,
                    "new_url": schedule_pages.NEW,
                    "delete_url": schedule_pages.DELETE,
                    "suggested": rows,
                    "suggested_url": path,
                },
            ),
        )

    refused = _form(FINANCIAL_VALUES, data=FINANCIAL_VALUES | {"name": ""})
    refused.is_valid()
    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        PATH: share({"settings"}),
        DIRECT: share({"settings"}, direct=True),
        DIRECT_RETRY: share({"settings"}, direct=True, page=DIRECT_RETRY),
        DIRECT_SETTINGS: settings(_form(FINANCIAL_VALUES)),
        DIRECT_SETTINGS_REFUSED: settings(refused),
        CONTENT: content(
            "content-catalog.html",
            {
                "pages": [],
                "emails": [],
                "base_digest": "a" * 64,
                "refresh_url": CONTENT,
            },
        ),
        EDITOR: content(
            "content-settings.html",
            {
                "label": "Welcome page",
                "visual": "<p>Welcome</p>",
                "placeholders": ["family_name"],
                "form": ContentForm(
                    kind="page",
                    slot="welcome",
                    initial={"base_digest": "a" * 64, "html": "<p>Welcome</p>"},
                ),
                "post_url": EDITOR,
            },
            sub=True,
        ),
        EDITOR_DIRECT: content(
            "content-settings.html",
            editor(
                ContentForm(kind="page", slot="welcome", initial=welcome),
                EDITOR_DIRECT,
            ),
            sub=True,
            direct=True,
        ),
        EDITOR_DIRECT_REFUSED: content(
            "content-settings.html",
            editor(unknown, EDITOR_DIRECT),
            sub=True,
            direct=True,
        ),
        SCHEDULES: schedule(context, SCHEDULES, current=None),
        NEW_DIRECT: schedule(context, NEW_DIRECT),
        EDIT_DIRECT: schedule(context, EDIT_DIRECT, schedule_row=True),
        SUGGESTED: suggested(),
        SUGGESTED_PASSED: suggested(SUGGESTED_PASSED, schedule_pages.NOW),
        REVIEW: (
            "text/html",
            render_to_string(
                "stewardship/share-options-review.html",
                {
                    "review": {
                        "changes": option_changes(options, renamed),
                        "preview": "synthetic-signed-intent",
                    }
                },
            ),
        ),
        APPLIED: (
            "text/html",
            render_to_string(
                "stewardship/configuration-request.html",
                context
                | {
                    "admin_chrome": admin,
                    "receipt": applied,
                    "continue_step": stepper({"settings", "share_options"}).next,
                },
            ),
        ),
        SETTLED: share(
            {"settings", "share_options"}, previous=renamed, digest="b" * 64
        ),
    }
