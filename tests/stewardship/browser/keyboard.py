"""Keyboard helpers shared by the browser tests."""

import sys


def tab_to_next(page):
    """Move focus to the next focusable element, links included (#1043).

    WebKit on macOS follows Safari's default keyboard setting, under which
    Tab skips links and Option+Tab reaches them; WebKit on Linux (CI),
    Chromium and Firefox all reach links with Tab. Pressing the key each
    platform needs keeps a keyboard-reachability check meaningful
    everywhere, without changing the page.
    """
    webkit = page.context.browser.browser_type.name == "webkit"
    page.keyboard.press("Alt+Tab" if webkit and sys.platform == "darwin" else "Tab")
