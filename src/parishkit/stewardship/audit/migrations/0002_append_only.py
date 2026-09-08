"""Enforce append-only audit history even when callers bypass the ORM.

TRUNCATE is intentionally not covered: migration/test database owners can reset
disposable databases. Runtime database grants must not include TRUNCATE; the
least-privilege runtime role installation is owned by OPS-02/OPS-04.
"""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_audit", "0001_initial")]
    operations = [immutable_guard_v1("stewardship_audit_event")]
