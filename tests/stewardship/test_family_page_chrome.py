"""The Family form's title."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string

from parishkit.stewardship.responses import availability


def _service(monkeypatch, *, campaign_name, parish_name):
    """A stand-in configuration with an optional campaign and parish name."""
    campaign = (
        None
        if campaign_name is None
        else SimpleNamespace(active_configuration=SimpleNamespace(name=campaign_name))
    )
    parish = None if parish_name is None else SimpleNamespace(name=parish_name)
    monkeypatch.setattr(
        availability,
        "coherent_configuration",
        lambda store: SimpleNamespace(
            current_campaign=campaign,
            active_configuration=SimpleNamespace(parish=parish),
        ),
    )
    return SimpleNamespace(store=object())


@pytest.mark.parametrize(
    "campaign_name,parish_name,title",
    [
        ("Stewardship 2027", "St. Example", "Stewardship 2027"),
        ("", "St. Example", "St. Example"),
        (None, "St. Example", "St. Example"),
        (None, None, "Stewardship"),
    ],
)
def test_family_portal_title_prefers_campaign_then_parish(
    monkeypatch, campaign_name, parish_name, title
):
    """The generic "Family campaign" heading is gone (#220)."""
    service = _service(
        monkeypatch, campaign_name=campaign_name, parish_name=parish_name
    )
    assert availability.family_portal_title(service) == title


def test_family_form_shows_campaign_title():
    """The form's <h1> and <title> carry the campaign name."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    html = render_to_string(
        "stewardship/family.html",
        {
            "portal_title": "Stewardship 2027",
            "server_now": now,
            "deadline": now + timedelta(hours=1),
            "absolute_deadline": now + timedelta(hours=4),
        },
    )
    assert "<h1>Stewardship 2027</h1>" in html
    assert "<title>Stewardship 2027" in html
    assert "Family campaign" not in html
