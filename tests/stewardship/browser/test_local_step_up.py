"""Fresh-authentication prompts in LOCAL and elsewhere, in Chromium and WebKit.

LOCAL has no Google sign-in (#619): every prompt shows the laptop sign-in
command and no visible text on the page mentions Google. Outside LOCAL the
same prompt keeps its "Confirm with Google" button and its Google wording.
"""

import pytest

from .step_up_components import LOCAL_WORDING, PAGES
from .waits import visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "webkit"], indirect=True
)

# The page's visible text: hidden panels (such as the finishing page's
# completion panel) are not part of it.
VISIBLE_TEXT = "document.body.innerText"


@pytest.mark.parametrize("name", sorted(PAGES))
def test_local_prompt_shows_the_command_and_never_google(page, component_origin, name):
    """In LOCAL the prompt names the laptop command and the page never Google."""
    page.goto(component_origin + f"/step-up/{name}/local")
    step_up = page.locator("[data-local-step-up]")
    visible(step_up)
    assert (
        "tools/stewardship-local.sh sign-in --email you@example.org"
        in step_up.inner_text()
    )
    assert page.get_by_role("button", name="Confirm with Google").count() == 0
    text = page.evaluate(VISIBLE_TEXT)
    assert "Google" not in text
    for sentence in LOCAL_WORDING[name]:
        assert sentence in text


@pytest.mark.parametrize("name", sorted(PAGES))
def test_production_prompt_keeps_google(page, component_origin, name):
    """Outside LOCAL the prompt keeps its Google button and wording."""
    page.goto(component_origin + f"/step-up/{name}")
    visible(page.get_by_role("button", name="Confirm with Google"))
    assert page.locator("[data-local-step-up]").count() == 0
    text = page.evaluate(VISIBLE_TEXT)
    for sentence in PAGES[name][2]:
        assert sentence in text
