"""The Mail message page's per-address counts, without a database (#806)."""

from pathlib import Path

from parishkit.stewardship.jobs.delivery_metadata import AddressOutcome


def test_reached_and_partial_follow_the_counts():
    """Reached is what the provider did not refuse; partial when any was."""
    whole = AddressOutcome(2, 0, 0)
    assert whole.reached == 2 and not whole.partial
    one_for_now = AddressOutcome(2, 1, 0)
    assert one_for_now.reached == 1 and one_for_now.partial
    mixed = AddressOutcome(3, 1, 1)
    assert mixed.reached == 1 and mixed.partial
    assert AddressOutcome(2, 1, 0) == one_for_now != whole
    assert "transient=1" in repr(one_for_now)


def test_the_page_template_names_counts_never_addresses():
    """The notice and the history label use counts and the Refused addresses link."""
    text = (
        Path(__file__).resolve().parents[2]
        / "src/parishkit/stewardship/accounts/templates/stewardship/delivery.html"
    ).read_text(encoding="utf-8")
    assert "addresses.partial" in text and "delivery_refusals" in text
    assert "routed_recipients" not in text and "intended_recipients" not in text
