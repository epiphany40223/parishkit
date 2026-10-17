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
``LOAD_FAILED`` is the same after a failed load (Try again), and
``LOADING`` is either one's in-place answer once a load is requested, whose
action row offers Continue to Share options. Its form posts to the real
refresh address, which another component already answers, so the test
answers that POST with ``LOADING`` itself.
"""

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

    def load(state):
        """Load ParishSoft data with the campaign's data ``state`` (PR 5)."""
        chrome = admin | {
            "campaign_wizard": campaign_wizard.build(
                True,
                "load",
                done={"settings"},
                mail_test_url=TEST_URL,
                load=state,
                load_task=UUID(int=1404) if state == "failed" else None,
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
                },
            ),
        )

    applied = SimpleNamespace(state="applied", request_id=REQUEST, failure_code="")
    return {
        LOAD: load("offer"),
        LOAD_FAILED: load("failed"),
        LOADING: load("loading"),
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
