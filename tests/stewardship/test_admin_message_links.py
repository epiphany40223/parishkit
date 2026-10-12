"""Admin messages link the pages they name (#521, navigation rule 6).

A message that tells the reader to use another page links that page when the
viewer may open it. Each test renders one page's template with just enough
context to show the message.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from django.template.loader import render_to_string
from django.urls import reverse


def _production_progress(complete):
    """Render Production activation with initial mail being prepared or done."""
    now = datetime(2054, 10, 1, tzinfo=UTC)
    return render_to_string(
        "stewardship/production-progress.html",
        {
            "admin_chrome": None,
            "campaign": SimpleNamespace(
                state="active", active_configuration=SimpleNamespace(name="Annual")
            ),
            "receipt": SimpleNamespace(created_at=now),
            "demand": SimpleNamespace(
                items_completed=3, phase="plan", groups_completed=1, failure_code=""
            ),
            "task": SimpleNamespace(state="running", updated_at=now),
            "complete": complete,
            "outcomes": [],
        },
    )


@pytest.mark.parametrize("complete", [False, True])
def test_production_activation_links_outgoing_mail(complete):
    """Both "see Outgoing mail" messages link Outgoing mail once preparation shows."""
    html = _production_progress(complete)
    link = f'<a href="{reverse("admin:deliveries")}">Outgoing mail</a>'
    # The outcomes note always shows; the completion note only once complete.
    assert html.count(link) == (2 if complete else 1)
    assert f"for that, see {link}." in html
    assert "See Outgoing mail" not in html
    assert "see Outgoing mail" not in html
