"""Family names from one source snapshot: surname, then active heads."""

from types import SimpleNamespace

from parishkit.stewardship.source import snapshot_names


class _Payloads:
    """A stand-in for the one snapshot read, returning canned payloads.

    ``usable`` False stands for a compacted (or unpromoted) snapshot, which
    the real statement reports instead of any rows.
    """

    def __init__(self, families, members, usable=True):
        self.families, self.members, self.usable = families, members, usable
        self.calls = []

    def __call__(self, snapshot_id, keys):
        """Record the read and return the requested Families and their heads."""
        self.calls.append((snapshot_id, set(keys)))
        if not self.usable:
            return None
        families = {key: self.families[key] for key in keys if key in self.families}
        heads = {
            str(head)
            for values in families.values()
            for head in values.get("active_head_duids") or ()
        }
        members = {key: value for key, value in self.members.items() if key in heads}
        return families, members


def _install(monkeypatch, families, members, usable=True):
    """Point the helper at canned Family and Member snapshot payloads."""
    read = _Payloads(families, members, usable)
    monkeypatch.setattr(snapshot_names, "_read_payloads", read)
    return read


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
    read = _install(monkeypatch, families, members)
    names = snapshot_names.snapshot_family_names("snap", [1, 2, 3])
    assert names == {1: "Squyres, Jeff and Tracy", 2: "Reiss"}
    # One read serves every Family, whatever the page size.
    assert read.calls == [("snap", {"1", "2", "3"})]


def test_no_snapshot_or_no_duids_needs_no_query(monkeypatch):
    """Nothing to name means nothing is read."""
    read = _install(monkeypatch, {}, {})
    assert snapshot_names.snapshot_family_names(None, [1]) == {}
    assert snapshot_names.snapshot_family_names("snap", []) == {}
    assert read.calls == []


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


def test_facts_add_the_envelope_number_and_mailing_name(monkeypatch):
    """The response lists read the envelope and mailing name with the name."""
    _install(
        monkeypatch,
        {
            "1": {"lastName": "Lee", "mailingName": " Ann Lee ", "envelopeNumber": 0},
            "2": {"lastName": "Ray", "envelopeNumber": True},
            "3": {"lastName": "Fox", "mailingName": 7, "envelopeNumber": 42},
        },
        {},
    )
    facts = snapshot_names.snapshot_family_facts("snap", [1, 2, 3])
    assert facts == {
        1: snapshot_names.FamilyFacts("Lee", 0, "Ann Lee"),
        # A missing or non-integer envelope is None; a non-text name is blank.
        2: snapshot_names.FamilyFacts("Ray", None, ""),
        3: snapshot_names.FamilyFacts("Fox", 42, ""),
    }


def test_name_rows_gives_report_rows_the_directory_name(monkeypatch):
    """Surname-only report rows read "Squyres, Jeff and Tracy" (#932).

    Rows of one Family share one lookup, and a Family missing from the
    snapshot keeps the name its report read. Even with no rows the snapshot
    is read, so the caller still learns which name form applies.
    """
    families = {"1": {"lastName": "Squyres", "active_head_duids": [10, 20]}}
    members = {
        "10": {"firstName": "Jeff", "lastName": "Squyres", "active": True},
        "20": {"firstName": "Tracy", "lastName": "Squyres", "active": True},
    }
    read = _install(monkeypatch, families, members)
    rows = [
        {"family_duid": 1, "family_name": "Squyres"},
        {"family_duid": 1, "family_name": "Squyres"},
        {"family_duid": 9, "family_name": "Unavailable Family"},
    ]
    assert snapshot_names.name_rows("snap", rows) is True
    assert [row["family_name"] for row in rows] == [
        "Squyres, Jeff and Tracy",
        "Squyres, Jeff and Tracy",
        "Unavailable Family",
    ]
    assert read.calls == [("snap", {"1", "9"})]
    assert snapshot_names.name_rows("snap", []) is True
    assert snapshot_names.name_rows(None, []) is False


def test_a_compacted_snapshot_names_no_family(monkeypatch):
    """Once compaction has started, every row keeps its surname (#932).

    The read reports the snapshot unusable rather than whichever rows the
    reclaimer has not deleted yet, so no file mixes the two name forms.
    """
    families = {"1": {"lastName": "Squyres", "active_head_duids": [10]}}
    members = {"10": {"firstName": "Jeff", "lastName": "Squyres", "active": True}}
    _install(monkeypatch, families, members, usable=False)
    rows = [{"family_duid": 1, "family_name": "Squyres"}]
    assert snapshot_names.name_rows("snap", rows) is False
    assert rows == [{"family_duid": 1, "family_name": "Squyres"}]
    assert snapshot_names.snapshot_family_facts("snap", [1]) == {}


def test_name_file_rows_records_the_form_the_file_used(monkeypatch):
    """The "Family names" detail says which form a file's rows use."""
    families = {"1": {"lastName": "Lee", "active_head_duids": []}}
    metadata = {"source_id": "snap"}
    _install(monkeypatch, families, {})
    snapshot_names.name_file_rows(metadata, [{"family_duid": 1, "family_name": "Lee"}])
    assert metadata["family_names"] == snapshot_names.FAMILY_NAMES_FULL
    _install(monkeypatch, families, {}, usable=False)
    snapshot_names.name_file_rows(metadata, [{"family_duid": 1, "family_name": "Lee"}])
    assert metadata["family_names"] == snapshot_names.FAMILY_NAMES_SURNAME
