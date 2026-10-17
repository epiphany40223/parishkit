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
"""

from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

from django.template.loader import render_to_string
from django.urls import reverse

from parishkit.stewardship.accounts import campaign_wizard
from parishkit.stewardship.accounts.content_forms import ContentForm
from parishkit.stewardship.accounts.option_editing import option_changes
from parishkit.stewardship.accounts.share_forms import (
    ShareOptions,
    default_share_options,
)
from parishkit.stewardship.source.data_age import Connection
from parishkit.stewardship.source.refresh_status import FullRefreshStatus

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
LOAD = "/campaign-stepper-load"
LOAD_FAILED = "/campaign-stepper-load-failed"
LOADING = "/campaign-stepper-loading"
LOADED = "/campaign-stepper-loaded"
LOAD_RUN = UUID(int=1405)
LOAD_STATUS = reverse("admin:background_task_status", args=[LOAD_RUN])
LOAD_STATUS_RUNNING = "/campaign-stepper-load-status-running"
LOAD_STATUS_DONE = "/campaign-stepper-load-status-done"
POSTS = {
    PATH: (303, APPLIED, ""),
    reverse("admin:campaign_step_continue"): (303, CONTENT, ""),
}


def components(context, admin):
    """Share options with the Campaign setup stepper, through Apply."""
    options = default_share_options()
    renamed = [dict(options[0], label="Renamed option"), *options[1:]]

    def stepper(done, current="share_options", sub=False):
        """The stepper on ``current`` with ``done`` completed."""
        return campaign_wizard.build(
            True, current, done=done, mail_test_url=TEST_URL, sub=sub
        )

    def content(template, values, sub=False):
        """A Pages and emails page (``sub``: its editor), Share options kept."""
        chrome = admin | {
            "campaign_wizard": stepper({"settings", "share_options"}, "content", sub)
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

    def share(done, previous=options, digest="a" * 64):
        """Share options with its stepper, saved as ``previous``."""
        return (
            "text/html",
            render_to_string(
                "stewardship/share-settings.html",
                context
                | {
                    "admin_chrome": admin | {"campaign_wizard": stepper(done)},
                    "campaign": {
                        "pk": uuid4(),
                        "active_configuration": {"name": "Sample campaign"},
                    },
                    "base_digest": digest,
                    "formset": ShareOptions(prefix="options", previous=previous),
                    "page_url": PATH,
                    "refresh_url": SETTLED,
                },
            ),
        )

    now = context["server_now"]

    def load(state):
        """Load ParishSoft data with the campaign's data ``state`` (PR 5):
        offer, loading (the run just queued), failed (in Save the downloaded
        records) or None, loaded with 1,234 Families."""
        task = {"failed": UUID(int=1404), "loading": LOAD_RUN}.get(state)
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

    def load_status(state, phase):
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
                campaign_wizard.load_status(task),
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        LOAD: load("offer"),
        LOAD_FAILED: load("failed"),
        LOADING: load("loading"),
        LOADED: load(None),
        LOAD_STATUS_RUNNING: load_status("running", "staging"),
        LOAD_STATUS_DONE: load_status("succeeded", "promoting"),
        PATH: share({"settings"}),
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
