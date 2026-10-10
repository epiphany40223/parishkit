"""The SQL Family name expression binds what its SQL expects."""

from types import SimpleNamespace

from parishkit.stewardship.source import family_names, snapshot_name_sql


def test_sql_trim_set_is_every_python_whitespace_character():
    """SQL trims exactly what str.strip() removes, so both names agree."""
    expected = "".join(chr(code) for code in range(0x110000) if chr(code).isspace())
    assert "".join(sorted(family_names.WHITESPACE)) == expected
    assert snapshot_name_sql.WHITESPACE is family_names.WHITESPACE


def test_sql_name_binds_one_parameter_per_placeholder():
    """Both shapes bind the trim sets, default, snapshot and the DUID's params."""
    compiler = SimpleNamespace(compile=lambda _expression: ("%s", ["duid"]))
    for surname_only in (True, False):
        name = snapshot_name_sql.SnapshotFamilyName(
            "snap", "family_duid", default="fallback", surname_only=surname_only
        )
        sql, params = name.as_sql(compiler, None)
        assert sql.count("%s") == len(params)
        assert params[-3:] == ("fallback", "snap", "duid")
        assert ("active_head_duids" in sql) is not surname_only
