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

``LOAD`` is step 2, Load ParishSoft data (#1404 PR 5): Refresh from
ParishSoft with the stepper current on it and no load started yet;
``LOAD_FAILED`` is the same after a failed load (Try again), ``LOADING``
is either one's in-place answer once a load is requested (no button, no
Continue), and ``LOADED`` the page once the load has brought the
campaign's Families, whose action row offers Continue to Share options.
While loading, the step polls ``LOAD_RUN``'s passive status
(``LOAD_STATUS`` with ?view=load), answered by ``LOAD_STATUS_RUNNING`` and
then ``LOAD_STATUS_DONE``. Its form posts to the real refresh address,
which another component already answers, so the test answers that POST
(and the in-place re-read once the run ends) itself.

``EDITOR_KEEP`` is that editor opened on a listed item (#1404 PR 6b): its
action row also holds Keep as is and go back, posting to the step
Continue's real address (answered with ``CONTENT``), and its Save and go
back saves to itself (``APPLIED``). ``CONTENT`` lists the welcome page as
kept as is, with the row id both return to.
"""

from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.admin_editing import summary_errors
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.accounts.option_editing import option_changes
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

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
EDITOR_KEEP = "/campaign-stepper-editor-keep"
SCHEDULES = "/campaign-stepper-schedules"
NEW_DIRECT = "/campaign-stepper-new-direct"
EDIT_DIRECT = "/campaign-stepper-edit-direct"
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
LOAD = "/campaign-stepper-load"
LOAD_FAILED = "/campaign-stepper-load-failed"
LOADING = "/campaign-stepper-loading"
LOADED = "/campaign-stepper-loaded"
LOAD_EMPTY = "/campaign-stepper-load-empty"
LOAD_RUN = UUID(int=1405)
LOAD_STATUS = reverse("admin:background_task_status", args=[LOAD_RUN])
LOAD_STATUS_RUNNING = "/campaign-stepper-load-status-running"
LOAD_STATUS_DONE = "/campaign-stepper-load-status-done"
LOAD_STATUS_EMPTY = "/campaign-stepper-load-status-empty"
POSTS = {
    PATH: (303, APPLIED, ""),
    DIRECT: (303, APPLIED, ""),
    DIRECT_RETRY: (303, NOT_APPLIED, ""),
    DIRECT_SETTINGS: (303, APPLIED, ""),
    EDITOR_DIRECT: (303, APPLIED, ""),
    EDITOR_KEEP: (303, APPLIED, ""),
    NEW_DIRECT: (303, APPLIED, ""),
    EDIT_DIRECT: (303, APPLIED, ""),
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

    refused = _form(FINANCIAL_VALUES, data=FINANCIAL_VALUES | {"name": ""})
    refused.is_valid()
    now = context["server_now"]

    def load(state):
        """Load ParishSoft data with the campaign's data ``state`` (PR 5):
        offer, loading (the run just queued), failed (in Save the downloaded
        records), empty (loaded, no Families matched) or None, loaded with
        1,234 Families."""
        task = {"failed": UUID(int=1404), "loading": LOAD_RUN, "empty": LOAD_RUN}.get(
            state
        )
        progress = {"failed": ("staging", 10, 40), "loading": ("unspecified", 0, 0)}
        chrome = admin | {
            "campaign_wizard": campaign_wizard.build(
                True,
                "load",
                done={"settings"} | (set() if state else {"load"}),
                mail_test_url=TEST_URL,
                load=state,
                load_task=task,
                load_progress=progress.get(state),
                families=None if state else 1234,
            )
        }
        return (
            "text/html",
            render_to_string(
                "stewardship/source-refresh.html",
                context
                | {
                    "admin_chrome": chrome,
                    "pending": {"running": False, "waiting": False},
                    "request_key": uuid4(),
                    "lateness_minutes": 30,
                    "full_refresh": FullRefreshStatus(
                        now,
                        None,
                        False,
                        full_started_at=now - timedelta(minutes=1),
                        data_as_of=now - timedelta(minutes=1),
                        connection=Connection("working", now),
                    ),
                },
            ),
        )

    def load_status(state, phase, families=True):
        """The step's polled status for its run in ``state`` at ``phase``."""
        task = {
            "id": str(LOAD_RUN),
            "state": state,
            "progress": {"phase": phase, "current": 1000, "total": 4000},
        }
        return (
            "text/html",
            render_to_string(
                "stewardship/campaign-load-status.html",
                campaign_wizard.load_status(task, families=families),
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        LOAD: load("offer"),
        LOAD_FAILED: load("failed"),
        LOADING: load("loading"),
        LOADED: load(None),
        LOAD_EMPTY: load("empty"),
        LOAD_STATUS_RUNNING: load_status("running", "staging"),
        LOAD_STATUS_DONE: load_status("succeeded", "promoting"),
        LOAD_STATUS_EMPTY: load_status("succeeded", "promoting", families=False),
        PATH: share({"settings"}),
        DIRECT: share({"settings"}, direct=True),
        DIRECT_RETRY: share({"settings"}, direct=True, page=DIRECT_RETRY),
        DIRECT_SETTINGS: settings(_form(FINANCIAL_VALUES)),
        DIRECT_SETTINGS_REFUSED: settings(refused),
        CONTENT: content(
            "content-catalog.html",
            {
                "pages": [
                    {
                        "label": "Welcome page",
                        "url": EDITOR_KEEP,
                        "state": "default",
                        "kept": True,
                        "row_id": "content-page-welcome",
                    }
                ],
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
        EDITOR_KEEP: content(
            "content-settings.html",
            editor(
                ContentForm(kind="page", slot="welcome", initial=welcome),
                EDITOR_KEEP,
            )
            | {
                "keep_item": {"item": "page:welcome", "focus": "content-page-welcome"},
                "save_focus": "content-page-welcome",
            },
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
