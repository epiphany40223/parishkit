"""Apply provider intake policies to the schema owner as well as runtime roles."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0060_setup_draft_guards")]
    operations = [
        migrations.RunSQL(
            "ALTER TABLE public.stewardship_provider_context FORCE ROW LEVEL SECURITY;",
            "ALTER TABLE public.stewardship_provider_context "
            "NO FORCE ROW LEVEL SECURITY;",
        )
    ]
