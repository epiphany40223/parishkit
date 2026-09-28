"""The Family sign-in page title and the campaign-local submission time."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from parishkit.stewardship.responses import availability
from parishkit.stewardship.web.presentation import parish_instant


def _service(monkeypatch, modules, name=""):
    """A stand-in configuration whose current campaign has ``modules``."""
    campaign = (
        None
        if modules is None
        else SimpleNamespace(
            active_configuration=SimpleNamespace(name=name, values={"modules": modules})
        )
    )
    monkeypatch.setattr(
        availability,
        "coherent_configuration",
        lambda store: SimpleNamespace(current_campaign=campaign),
    )
    return SimpleNamespace(store=object())


@pytest.mark.parametrize(
    "modules,title",
    [
        (["ministry"], "Family stewardship login"),
        (["financial"], "Family stewardship login"),
        (["financial", "ministry"], "Family stewardship login"),
        (["census"], "Family census login"),
        (["census", "ministry"], "Family stewardship and census login"),
        (["census", "financial", "ministry"], "Family stewardship and census login"),
        (None, "Family login"),
    ],
)
def test_login_title_follows_campaign_modules(monkeypatch, modules, title):
    """An unnamed campaign's title says what it collects."""
    assert availability.family_login_title(_service(monkeypatch, modules)) == title


def test_login_title_uses_the_campaign_name(monkeypatch):
    """A named campaign titles the sign-in page "<campaign name> login"."""
    service = _service(monkeypatch, ["financial"], name="Stewardship 2027")
    assert availability.family_login_title(service) == "Stewardship 2027 login"


@pytest.mark.parametrize(
    "instant,timezone,text",
    [
        (
            datetime(2026, 9, 28, 11, 15, tzinfo=UTC),
            "America/New_York",
            "September 28, 2026 at 7:15 AM EDT",
        ),
        (
            datetime(2026, 12, 1, 0, 5, tzinfo=UTC),
            "US/Eastern",
            "November 30, 2026 at 7:05 PM EST",
        ),
        (
            datetime(2026, 9, 28, 12, 0, tzinfo=UTC),
            "UTC",
            "September 28, 2026 at 12:00 PM UTC",
        ),
    ],
)
def test_parish_instant_uses_the_campaign_time_zone(instant, timezone, text):
    """Every viewer sees the same campaign-local wall-clock time."""
    assert parish_instant(instant, timezone) == text
