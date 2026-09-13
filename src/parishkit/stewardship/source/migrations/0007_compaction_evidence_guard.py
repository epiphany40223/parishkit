"""Compaction history is permanent metadata, not a disposable cleanup artifact."""

from django.db import migrations

from parishkit.stewardship.storage_migrations import immutable_guard_v1


class Migration(migrations.Migration):
    dependencies = [("stewardship_source", "0006_sourcecompactionbatch_and_more")]
    operations = [immutable_guard_v1("stewardship_source_compaction")]
