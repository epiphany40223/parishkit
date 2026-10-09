"""Wording and caching of the Families-the-form-cannot-open list (#774)."""

from parishkit.stewardship.reports import source_form
from parishkit.stewardship.source_form_check import RECORD, VALUE


def test_field_labels_are_the_forms_plain_words():
    """A field reads as the census form labels it; a record finding says so."""
    assert source_form.field_label("birth_date", VALUE) == "Birth date"
    assert source_form.field_label("mobile_phone", VALUE) == "Mobile phone"
    assert source_form.field_label("member", RECORD) == source_form.RECORD_LABEL
    assert source_form.field_label("email", RECORD).startswith("Email address (")


def test_the_cache_key_is_campaign_snapshot_and_configuration():
    """Any of the three changing is a new key; the cache keeps a few only."""
    one = {"snapshot_id": 1, "configuration_id": 2}
    assert source_form.cache_key(one, "c") == ("c", "1", "2")
    assert source_form.cache_key(one | {"snapshot_id": 3}, "c") != (
        source_form.cache_key(one, "c")
    )
    source_form._cache.clear()
    for number in range(source_form.CACHE_SIZE + 2):
        source_form._remember(("k", number), {"n": number})
    assert len(source_form._cache) == source_form.CACHE_SIZE
    assert source_form._cached(("k", 0)) is None
    assert source_form._cached(("k", source_form.CACHE_SIZE + 1)) == {
        "n": source_form.CACHE_SIZE + 1
    }
    source_form._cache.clear()


def test_a_configuration_change_is_a_new_scan(monkeypatch):
    """A cached result is reused for the same identity and recomputed when
    the campaign configuration changes (the census module turned on or off),
    without loading any Member on a hit."""
    identity = {"snapshot_id": "s", "configuration_id": "one", "census": True}
    loads = []

    def inputs(campaign_id):
        """The scan's inputs: no Families, at the current identity."""
        loads.append(identity["configuration_id"])
        return identity | {"families": [], "members": [], "contacts": {}}

    monkeypatch.setattr(source_form, "scan_identity", lambda campaign_id: identity)
    monkeypatch.setattr(source_form, "scan_inputs", inputs)
    source_form._cache.clear()
    assert source_form.blocked_families("c")["rows"] == []
    source_form.blocked_families("c")
    assert loads == ["one"]
    identity = identity | {"configuration_id": "two", "census": False}
    source_form.blocked_families("c")
    assert loads == ["one", "two"]
    source_form._cache.clear()
