"""Shared helpers for browser tests of editors that review a change (#750).

``answer_reviews`` serves a fixture as the answer to a page's Review POST,
``expect_review`` checks the change gate on Review changes (#921), and
``RESTORE`` replays a back/forward cache restore (``pageshow``).
"""

RESTORE = "window.dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}))"
HINT = "#settings-form-unchanged-hint"


def answer_reviews(page, origin, fixture, status=200, *, address):
    """Answer ``address``'s Review POSTs with ``fixture``; Apply goes through.

    Returns the list of Review POST bodies answered.
    """
    reviews = []

    def answer(route):
        """Serve ``fixture`` for a Review; let loads and Apply through."""
        request = route.request
        if request.method != "POST" or "action=preview" not in (
            request.post_data or ""
        ):
            route.continue_()
            return
        reviews.append(request.post_data)
        fetched = route.fetch(url=origin + fixture, method="GET")
        route.fulfill(response=fetched, status=status)

    page.route(lambda url: url.split("#")[0] == origin + address, answer)
    return reviews


def expect_review(
    page, *, enabled, hint="Change a setting to review it.", name="Review changes"
):
    """Review changes (or a confirmation's own label, ``name``, such as Save,
    #1356) is available exactly when the form differs from what is saved
    (#921); otherwise its hint, which always keeps its space, is shown and
    describes it."""
    from playwright.sync_api import expect

    button = page.get_by_role("button", name=name, exact=True)
    line = page.locator(HINT)
    expect(line).to_have_text(hint)
    if enabled:
        expect(button).to_be_enabled()
        expect(line).to_have_css("visibility", "hidden")
        expect(button).not_to_have_attribute("aria-describedby", HINT[1:])
    else:
        expect(button).to_be_disabled()
        expect(line).to_have_css("visibility", "visible")
        expect(button).to_have_accessible_description(hint)
