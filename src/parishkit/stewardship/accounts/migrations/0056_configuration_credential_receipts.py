"""Permit only public target-receipt reads for integration selection verification."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0055_branding_guards")]
    operations = [
        migrations.RunSQL(
            """
        CREATE POLICY stewardship_config_secret_read
        ON public.stewardship_secret_request FOR SELECT
        USING (current_user='pk_stewardship_config_installer'
            AND target IN('parishsoft','google_workspace','slack'));
        CREATE POLICY stewardship_config_provider_read
        ON public.stewardship_provider_context FOR SELECT
        USING (current_user='pk_stewardship_config_installer'
            AND target IN('parishsoft','google_workspace','slack'));
        """,
            """
        DROP POLICY stewardship_config_provider_read
            ON public.stewardship_provider_context;
        DROP POLICY stewardship_config_secret_read ON public.stewardship_secret_request;
        """,
        )
    ]
