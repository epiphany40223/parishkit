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
"""

from dataclasses import replace
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
    reverse("admin:campaign_step_continue"): (303, CONTENT, ""),
}


def components(context, admin):
    """Share options with the Campaign setup stepper, through Apply."""
    options = default_share_options()
    renamed = [dict(options[0], label="Renamed option"), *options[1:]]

    def stepper(done, current="share_options", sub=False, direct=False):
        """The stepper on ``current`` with ``done`` completed.

        A ``direct`` one goes on to ``CONTENT``, the component Pages and
        emails, rather than the real address.
        """
        wizard = campaign_wizard.build(
            True, current, done=done, mail_test_url=TEST_URL, sub=sub, direct=direct
        )
        if direct:
            wizard = replace(wizard, upcoming=replace(wizard.upcoming, url=CONTENT))
        return wizard

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
