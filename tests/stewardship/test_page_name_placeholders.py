"""The Family page fills the name placeholders from heads and all Members (#471)."""

from types import SimpleNamespace

from parishkit.stewardship.responses import presentation


def test_page_names_heads_and_every_member_separately(monkeypatch):
    """head_salutation names only heads; all_family_member_names names everyone."""
    captured = {}
    monkeypatch.setattr(presentation, "public_substitutions", lambda parish, c: {})
    monkeypatch.setattr(presentation, "family_page_slots", lambda values: [])

    def render(configuration_id, campaign, slots, substitutions):
        """Record the substitutions the page would render with."""
        captured.update(substitutions)
        return {}

    monkeypatch.setattr(presentation, "render_pages", render)
    names = [
        {"first": "Andrew", "last": "Test", "head": True},
        {"first": "Betty", "last": "Test", "head": True},
        {"first": "Cy", "last": "Test", "head": False},
    ]
    presentation._page_content(
        SimpleNamespace(configuration=SimpleNamespace(parish=None), configuration_id=1),
        SimpleNamespace(values={}),
        {"lastName": "Test"},
        names,
        3,
        None,
    )
    assert captured["head_salutation"] == "Andrew and Betty Test"
    assert captured["family_member_names"] == "Andrew and Betty Test"
    assert captured["all_family_member_names"] == "Andrew, Betty and Cy Test"
    assert captured["family_name"] == "Test"
