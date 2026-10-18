"""The one Slack notifications setup step over the deployed web grants (#1578).

The step saves Enable Slack notifications and the channel ID and stages the
Slack bot token in one work transaction, under the exact draft version. A
saved token is kept by leaving the field empty for its own channel; a new
channel needs it entered again; a malformed token saves nothing; turning
Slack off discards it (#1499).
"""

# ruff: noqa: F811 -- pytest resolves the imported fixture dependencies.

import pytest

from parishkit.stewardship.accounts.setup_models import SetupAttempt, SetupDraftSection
from parishkit.stewardship.accounts.setup_secret_models import SetupSealedCredential

from .test_bootstrap_postgresql import bootstrapped  # noqa: F401
from .test_runtime_auth_grants_postgresql import web_login
from .test_setup_credentials_postgresql import publish
from .test_setup_views_postgresql import post, setup_http, started  # noqa: F401

pytestmark = pytest.mark.django_db(transaction=True)
STEP = "/admin/setup/slack"
TOKEN = "xoxb-synthetic-slack-token"


def version():
    """The attempt's current optimistic-lock version as a form value."""
    return str(SetupAttempt.objects.get().version)


def save(browser, channel="CEXAMPLE", token=""):
    """Post the Slack step on, with ``channel`` and ``token`` (empty: kept)."""
    values = {"enabled": "on", "channel_id": channel, "version": version()}
    return post(browser, STEP, values | ({"candidate": token} if token else {}))


def staged():
    """The unscrubbed staged Slack token's (version, channel), or None.

    Only the receipt columns the web login may read are selected.
    """
    row = (
        SetupSealedCredential.objects.filter(target="slack", scrubbed_at=None)
        .values_list("version", "settings")
        .first()
    )
    return row and (row[0], row[1]["channel_id"])


def saved_channel():
    """The Slack step's saved channel, or None before it is saved."""
    row = SetupDraftSection.objects.filter(step="slack").first()
    return row and row.values["channel_id"]


def test_one_save_stores_the_channel_and_stages_the_token(setup_http, google):
    """Save and continue saves the step and stages the token together, then
    opens Test Slack; the page says a token is saved and never shows it."""
    publish("slack")
    with web_login():
        browser = started()
        before = SetupAttempt.objects.get().version
        response = save(browser, token=TOKEN)
        assert response.status_code == 302
        assert response["Location"] == "/admin/setup/slack-test"
        assert saved_channel() == "CEXAMPLE" and staged() == (1, "CEXAMPLE")
        # The section save and the staging each advance the version once.
        assert SetupAttempt.objects.get().version == before + 2
        page = browser.get(STEP)
        assert page.status_code == 200
        assert b"A Slack bot token is saved for this channel." in page.content
        assert TOKEN.encode() not in page.content
        assert b"data-setup-slack-off" in page.content
        # Keeping it: the same channel with the field empty stages nothing.
        kept = save(browser)
        assert kept.status_code == 302 and staged() == (1, "CEXAMPLE")


@pytest.mark.parametrize(
    ("first", "channel", "message"),
    [
        (False, "CEXAMPLE", b"Paste the Slack bot token to continue."),
        (
            True,
            "CANOTHER",
            b"To change the Slack channel ID, enter the Slack bot token again",
        ),
    ],
)
def test_slack_on_without_a_token_for_its_channel_is_refused(
    setup_http, google, first, channel, message
):
    """With Slack on, an empty token field keeps only a token saved for this
    channel: without one, or with a new channel, the field says the token is
    needed and nothing is saved."""
    publish("slack")
    with web_login():
        browser = started()
        if first:
            assert save(browser, token=TOKEN).status_code == 302
        before = (SetupAttempt.objects.get().version, saved_channel(), staged())
        refused = save(browser, channel=channel)
        assert refused.status_code == 400
        assert message in refused.content
        assert (SetupAttempt.objects.get().version, saved_channel(), staged()) == (
            before
        )
        if first:
            # With the token entered again, the new channel is saved and the
            # token sealed with it.
            assert save(browser, channel=channel, token=TOKEN).status_code == 302
            assert saved_channel() == channel and staged() == (2, channel)


def test_a_malformed_token_saves_nothing(setup_http, google):
    """A token the staging owner refuses is a field error, and the step's
    switch and channel roll back with it."""
    publish("slack")
    with web_login():
        browser = started()
        before = SetupAttempt.objects.get().version
        refused = save(browser, token="not a slack token")
        assert refused.status_code == 400
        assert b"This does not look like a Slack bot token." in refused.content
        # The refused token is never echoed back into the page.
        assert b"not a slack token" not in refused.content
        assert SetupAttempt.objects.get().version == before
        assert saved_channel() is None and staged() is None


def test_turning_slack_off_discards_the_saved_token(setup_http, google):
    """Saving the step off discards the staged token (#1499); the page then
    says nothing is saved and offers no Turn off button."""
    publish("slack")
    with web_login():
        browser = started()
        assert save(browser, token=TOKEN).status_code == 302
        off = {"channel_id": "", "version": version()}
        assert post(browser, STEP, off).status_code == 302
        assert staged() is None
        assert SetupSealedCredential.objects.filter(
            target="slack", scrubbed_at__isnull=False
        ).exists()
        page = browser.get(STEP)
        assert page.status_code == 200
        assert b"A Slack bot token is saved" not in page.content
        assert b"data-setup-slack-off" not in page.content
