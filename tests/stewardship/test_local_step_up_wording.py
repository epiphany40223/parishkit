"""Fresh-authentication prompts never mention Google in LOCAL (#619).

LOCAL has no Google sign-in: the shared step-up component already shows the
laptop sign-in command there, so the sentences each page puts around it must
not talk about Google either. Every other profile keeps the Production
wording exactly as it was.
"""

from datetime import UTC, datetime, timedelta

import pytest

from .browser.step_up_components import LOCAL_WORDING, PAGES, prompt_text, render

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)
CONTEXT = {
    "server_now": NOW,
    "deadline": NOW + timedelta(hours=1),
    "absolute_deadline": NOW + timedelta(hours=4),
    "csrf_token": "a" * 64,
}
LOCAL_COMMAND = "tools/stewardship-local.sh sign-in --email you@example.org"


@pytest.mark.parametrize("name", sorted(PAGES))
def test_production_wording_is_unchanged(name):
    """Outside LOCAL each prompt keeps its Google sentences and button."""
    page = render(name, CONTEXT)
    for sentence in PAGES[name][2]:
        assert sentence in page
    for sentence in set(LOCAL_WORDING[name]) - set(PAGES[name][2]):
        assert sentence not in page
    assert "Confirm with Google" in page
    assert "stewardship-local.sh" not in page


@pytest.mark.parametrize("name", sorted(PAGES))
def test_local_prompt_never_mentions_google(name):
    """In LOCAL the prompt names the laptop command and says nothing of Google."""
    page = render(name, CONTEXT, local=True)
    prompt = prompt_text(page)
    assert LOCAL_COMMAND in prompt
    assert "Google" not in prompt
    for sentence in LOCAL_WORDING[name]:
        assert sentence in prompt
    assert "Confirm with Google" not in page


@pytest.mark.parametrize(
    "minutes,says",
    [
        (1, "confirming your sign-in (you signed in 1 minute ago)."),
        (7, "confirming your sign-in (you signed in 7 minutes ago)."),
        (None, "Sending tests to real Families requires confirming your sign-in."),
    ],
)
def test_family_test_prompt_counts_minutes_in_local(minutes, says):
    """The Family test prompt keeps its minute count, singular or plural."""
    local = render("family-tests", CONTEXT, local=True, signed_in_minutes=minutes)
    other = render("family-tests", CONTEXT, signed_in_minutes=minutes)
    assert says in local and "Google" not in local
    assert "with Google" in other and says not in other
