"""Sign out asks first while the Administrator owns a setup attempt (#2272).

On the setup overview (collecting) and the Finishing setup page, the menu's
Sign out and the page's own Sign out open the shared confirmation dialog
instead of signing out. Stay signed in and Escape change nothing and post
nothing; Sign out and cancel setup posts the logout with the owned attempt's
id. Opening the dialog moves nothing on the page.
"""

import pytest

from .setup_signout_components import ATTEMPT, COLLECTING, FINISHING
from .test_setup_mail import place
from .waits import focused, hidden, same_place, visible

pytestmark = pytest.mark.parametrize(
    "browser_engine", ["chromium", "firefox", "webkit"], indirect=True
)

# Both pages' Sign out forms, by where they are drawn.
WHERE = {
    "menu": ".admin-signout",
    "page": "main form[action='/admin/logout']",
}


def logouts(page):
    """Answer every logout POST with a plain page; return the bodies posted."""
    posted = []

    def respond(route):
        """Record the posted fields; nothing real is signed out."""
        posted.append(route.request.post_data or "")
        route.fulfill(content_type="text/html", body="<p>signed out</p>")

    page.route("**/admin/logout", respond)
    return posted


def sign_out_button(page, where):
    """The Sign out button, opening the narrow-screen menu when it is shut."""
    button = page.locator(WHERE[where]).get_by_role("button", name="Sign out")
    if where == "menu" and not button.is_visible():
        page.get_by_text("Menu", exact=True).first.click()
    return button


@pytest.mark.parametrize("width", [320, 1280])
@pytest.mark.parametrize("address", [COLLECTING, FINISHING])
@pytest.mark.parametrize("where", ["menu", "page"])
def test_sign_out_asks_first_and_stay_changes_nothing(
    page, component_origin, axe_source, width, address, where
):
    """The dialog names the consequence, has the safe choice focused, moves
    nothing, and Stay signed in or Escape post nothing and return focus."""
    from .test_components import axe_violations

    page.set_viewport_size({"width": width, "height": 900})
    posted = logouts(page)
    page.goto(component_origin + address)
    button = sign_out_button(page, where)
    above = place(page, "h1")
    button.click()
    dialog = page.get_by_role("dialog", name="Sign out?")
    visible(dialog)
    assert dialog.inner_text().startswith("Sign out?")
    visible(
        dialog.get_by_text(
            "Signing out cancels this setup. You'll need to start a new setup"
            " with a new sign-in."
        )
    )
    stay = dialog.get_by_role("button", name="Stay signed in")
    focused(stay)
    assert same_place(place(page, "h1"), above)
    assert axe_violations(page, axe_source) == []
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    stay.click()
    hidden(dialog)
    focused(button)
    assert posted == []
    # The busy-button handler never saw the prevented submit: Sign out stays
    # an ordinary button, neither disabled nor spinning.
    assert button.get_attribute("aria-disabled") is None
    assert "is-busy" not in (button.get_attribute("class") or "")
    # Escape is the same as Stay signed in.
    button.click()
    visible(dialog)
    page.keyboard.press("Escape")
    hidden(dialog)
    focused(button)
    assert same_place(place(page, "h1"), above)
    assert posted == []


@pytest.mark.parametrize("address", [COLLECTING, FINISHING])
@pytest.mark.parametrize("where", ["menu", "page"])
def test_sign_out_and_cancel_posts_the_owned_attempt(
    page, component_origin, address, where
):
    """Sign out and cancel setup posts the logout with this attempt's id."""
    posted = logouts(page)
    page.goto(component_origin + address)
    sign_out_button(page, where).click()
    dialog = page.get_by_role("dialog", name="Sign out?")
    visible(dialog)
    dialog.get_by_role("button", name="Sign out and cancel setup").click()
    page.wait_for_function("window.location.pathname === '/admin/logout'")
    assert len(posted) == 1
    assert f"cancel_setup={ATTEMPT}" in posted[0]
