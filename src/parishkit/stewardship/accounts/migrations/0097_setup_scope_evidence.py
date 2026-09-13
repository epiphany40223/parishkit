"""Expose setup equality evidence without granting public draft payload access."""

from django.db import migrations, models

# Native SQL layout is retained for comparison with its frozen predecessors.
# ruff: noqa: E501

DIGEST = "pg_catalog.encode(pg_catalog.sha256(pg_catalog.jsonb_send(%(expressions)s)), 'hex')"


def rewrite(editor, *, reverse=False):
    """Replace only value comparisons; all original-login and key checks remain."""
    substitutions = {
        "mail": [
            (
                """AND candidate.settings->>'sender'=mail.values->>'sender'
            AND candidate.settings->>'reply_to'=mail.values->>'reply_to'
            AND candidate.settings->>'delegated_email'=mail.values->>'delegated_email'
            AND candidate.settings->>'recipient'=testing.values->>'testing_recipient'""",
                """AND mail.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'sender',candidate.settings->>'sender',
                'reply_to',candidate.settings->>'reply_to',
                'delegated_email',candidate.settings->>'delegated_email'))),'hex')
            AND testing.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'testing_recipient',candidate.settings->>'recipient'))),'hex')""",
            )
        ],
        "slack": [
            (
                "AND slack.scrubbed_at IS NULL AND slack.values->'enabled'='true'::jsonb",
                "AND slack.scrubbed_at IS NULL",
            ),
            (
                "AND candidate.settings->>'channel_id'=slack.values->>'channel_id'",
                """AND slack.scope_digest=encode(sha256(jsonb_send(jsonb_build_object(
                'enabled',true,'channel_id',candidate.settings->>'channel_id'))),'hex')""",
            ),
        ],
    }
    for target, pairs in substitutions.items():
        signature = (
            f"public.stewardship_setup_{target}_live_v1(uuid,bigint,uuid,bigint,text)"
        )
        with editor.connection.cursor() as cursor:
            cursor.execute("SELECT pg_get_functiondef(%s::regprocedure)", [signature])
            definition = cursor.fetchone()[0]
        for before, after in pairs:
            if reverse:
                before, after = after, before
            if definition.count(before) != 1:
                raise RuntimeError("Setup equality predecessor differs.")
            definition = definition.replace(before, after)
        editor.execute(definition, params=None)


def forward(apps, editor):
    """Invoker guards read generated equality hashes, never foreign public drafts."""
    rewrite(editor)
    bootstrap_scope(editor)


def backward(apps, editor):
    """Restore old invoker expressions before removing the generated column."""
    rewrite(editor, reverse=True)
    bootstrap_scope(editor, reverse=True)


def bootstrap_scope(editor, *, reverse=False):
    """Reviewed owner visibility still requires the entire draft table to be empty."""
    before = "'stewardship_setup_sealed_credential')) THEN"
    after = (
        "'stewardship_setup_sealed_credential', "
        "'stewardship_setup_draft_section')) THEN"
    )
    if reverse:
        before, after = after, before
    with editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef("
            "'public.stewardship_bootstrap_empty_database()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
    if definition.count(before) != 1:
        raise RuntimeError("Setup draft bootstrap visibility predecessor differs.")
    editor.execute(definition.replace(before, after), params=None)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0096_source_cadence_schema")]
    operations = [
        migrations.AddField(
            model_name="setupdraftsection",
            name="scope_digest",
            field=models.GeneratedField(
                expression=models.Func(models.F("values"), template=DIGEST),
                output_field=models.CharField(max_length=64),
                db_persist=True,
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.RunSQL(
            """
            ALTER TABLE public.stewardship_setup_draft_section ENABLE ROW LEVEL SECURITY;
            ALTER TABLE public.stewardship_setup_draft_section FORCE ROW LEVEL SECURITY;
            CREATE POLICY setup_public_scope ON public.stewardship_setup_draft_section
                USING (current_user<>'pk_stewardship_credential_slack' OR step='slack')
                WITH CHECK (current_user<>'pk_stewardship_credential_slack');
            ALTER TABLE public.stewardship_public_credential_handoff FORCE ROW LEVEL SECURITY;
            """,
            """
            ALTER TABLE public.stewardship_public_credential_handoff NO FORCE ROW LEVEL SECURITY;
            DROP POLICY setup_public_scope ON public.stewardship_setup_draft_section;
            ALTER TABLE public.stewardship_setup_draft_section NO FORCE ROW LEVEL SECURITY;
            ALTER TABLE public.stewardship_setup_draft_section DISABLE ROW LEVEL SECURITY;
            """,
        ),
    ]
