"""Pure validation of reusable, frozen database-guard migration generation."""

import pytest

from parishkit.stewardship.storage_migrations import mutable_guard_v1


@pytest.mark.parametrize("name", ["", "Bad", "has space", 'quote"', "x" * 41, None])
def test_guard_builder_rejects_unsafe_or_long_names(name):
    """Only bounded internal identifiers may enter generated SQL."""
    with pytest.raises(ValueError):
        mutable_guard_v1(name)
    with pytest.raises(ValueError):
        mutable_guard_v1("test_record", frozen_fields=(name,))


def test_guard_builder_is_reusable_and_reverses_only_its_own_objects():
    """Independent tables receive equivalent rules without sharing object lifetime."""
    first = mutable_guard_v1("first_record", frozen_fields=("principal_id",))
    second = mutable_guard_v1("second_record")
    assert 'NEW."principal_id" IS DISTINCT FROM OLD."principal_id"' in first.sql
    assert "principal_id" not in second.sql
    assert "first_record" not in second.reverse_sql
    assert "second_record" not in first.reverse_sql
    for operation in (first, second):
        assert "NEW.version IS DISTINCT FROM OLD.version + 1" in operation.sql
        assert "NEW.updated_at := statement_timestamp()" in operation.sql
