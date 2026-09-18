"""Reuse expensive browser startup without sharing scenario contexts."""

from contextlib import contextmanager


class BrowserProcesses:
    """Cache Chromium/Firefox only; preserve the known WebKit process workaround."""

    def __init__(self, factory):
        """Import/start Playwright only after the browser opt-in fixture admits it."""
        self.factory = factory
        self.runner = None
        self.browsers = {}

    def close(self):
        """Release cached processes before a fresh WebKit driver or session exit."""
        errors = []
        for browser in self.browsers.values():
            try:
                browser.close()
            except Exception as error:
                errors.append(error)
        self.browsers.clear()
        if self.runner is not None:
            try:
                self.runner.stop()
            except Exception as error:
                errors.append(error)
            finally:
                self.runner = None
        if errors:
            raise ExceptionGroup("Browser test resource cleanup failed", errors)

    @contextmanager
    def acquire(self, engine):
        """A test may reuse a process, never another test's cookies/routes/clock."""
        if engine not in {"chromium", "firefox", "webkit"}:
            raise ValueError("Unsupported test browser")
        if engine == "webkit":
            # Keep both driver and browser fresh for the documented 64th-case
            # navigation hang. Do not nest sync Playwright event loops.
            self.close()
            with self.factory() as runner:
                browser = runner.webkit.launch()
                try:
                    yield browser
                finally:
                    browser.close()
            return
        if self.runner is None:
            self.runner = self.factory().start()
        if engine not in self.browsers:
            self.browsers[engine] = getattr(self.runner, engine).launch()
        browser = self.browsers[engine]
        try:
            yield browser
        finally:
            # Contexts are per scenario. A forgotten close must fail rather than
            # leave state available to later scenarios in this cached process.
            leaked = list(browser.contexts)
            for context in leaked:
                context.close()
            if leaked:
                raise AssertionError("Browser scenario leaked an isolated context")
