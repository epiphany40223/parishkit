"""Family names from one source snapshot: surname, then active heads."""

from types import SimpleNamespace

from parishkit.stewardship.source import snapshot_names


class _Rows:
    """A stand-in manager whose filter() returns canned snapshot rows."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def filter(self, *, snapshot_id, source_key__in):
        """Record the query and return the rows for the requested keys."""
        self.calls.append((snapshot_id, set(source_key__in)))
        rows = [
            SimpleNamespace(source_key=key, payload=SimpleNamespace(payload=value))
            for key, value in self.payloads.items()
            if key in set(source_key__in)
        ]
        return SimpleNamespace(select_related=lambda *_: rows)


def _install(monkeypatch, families, members):
    """Point the helper at canned Family and Member snapshot rows."""
    family_rows, member_rows = _Rows(families), _Rows(members)
    monkeypatch.setattr(
        snapshot_names, "SnapshotFamily", SimpleNamespace(objects=family_rows)
    )
    monkeypatch.setattr(
        snapshot_names, "SnapshotMember", SimpleNamespace(objects=member_rows)
    )
    return family_rows, member_rows


def test_names_lead_with_the_surname_and_skip_inactive_heads(monkeypatch):
    """Heads in DUID order; inactive or missing heads add nothing."""
    families = {
        "1": {"lastName": "Squyres", "active_head_duids": [20, 10, 30, 40]},
        "2": {"lastName": "Reiss", "active_head_duids": []},
    }
    members = {
        "10": {"firstName": "Jeff", "lastName": "Squyres", "active": True},
        "20": {"firstName": "Tracy", "lastName": "Squyres", "active": True},
        "30": {"firstName": "Old", "lastName": "Squyres", "active": False},
    }
    family_rows, member_rows = _install(monkeypatch, families, members)
    names = snapshot_names.snapshot_family_names("snap", [1, 2, 3])
    assert names == {1: "Squyres, Jeff and Tracy", 2: "Reiss"}
    # Two queries serve every Family, whatever the page size.
    assert len(family_rows.calls) == 1 and len(member_rows.calls) == 1
    assert member_rows.calls[0][1] == {"10", "20", "30", "40"}


def test_no_snapshot_or_no_duids_needs_no_query(monkeypatch):
    """Nothing to name means nothing is read."""
    family_rows, _ = _install(monkeypatch, {}, {})
    assert snapshot_names.snapshot_family_names(None, [1]) == {}
    assert snapshot_names.snapshot_family_names("snap", []) == {}
    assert family_rows.calls == []


def test_default_names_a_family_without_a_surname(monkeypatch):
    """The caller's default stands in for a missing surname."""
    _install(monkeypatch, {"5": {"active_head_duids": []}}, {})
    assert snapshot_names.snapshot_family_names("snap", [5], "Family") == {5: "Family"}


def test_presence_names_a_nameless_family_family(monkeypatch):
    """The presence page passes "Family" as the default, like the send page."""
    from parishkit.stewardship.accounts import presence

    current = SimpleNamespace(snapshot_id="snap", organization_id="org")
    monkeypatch.setattr(
        presence,
        "SourceCurrent",
        SimpleNamespace(objects=SimpleNamespace(first=lambda: current)),
    )
    _install(
        monkeypatch,
        {"7": {"active_head_duids": [70]}},
        {"70": {"firstName": "Jeff", "lastName": "Squyres", "active": True}},
    )
    configuration = SimpleNamespace(
        active_configuration=SimpleNamespace(
            canonical_document={
                "sections": {
                    "integrations": [
                        {
                            "values": {
                                "kind": "parishsoft",
                                "settings": {"organization_id": "org"},
                            }
                        }
                    ]
                }
            }
        )
    )
    rows = [SimpleNamespace(family=SimpleNamespace(family_duid=7))]
    assert presence._names(configuration, rows) == {7: "Family, Jeff Squyres"}
