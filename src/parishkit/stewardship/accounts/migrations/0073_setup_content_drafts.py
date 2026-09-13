"""Bounded, original-attempt named content staging before configuration exists."""

from importlib import import_module

from django.db import migrations, models

_prior = import_module(
    "parishkit.stewardship.accounts.migrations.0072_setup_campaign_draft"
)
BACKWARD = _prior.FORWARD
STEPS = [
    "parish",
    "branding",
    "access",
    "mail",
    "slack",
    "testing",
    "campaign",
    "page_access_denied",
    "page_additional",
    "page_census",
    "page_financial",
    "page_login_help",
    "page_member_census",
    "page_ministry",
    "page_post_end",
    "page_pre_start",
    "page_review",
    "page_submission_confirmation",
    "page_thank_you",
    "page_welcome",
    "email_confirmation",
    "email_critical_alert",
    "email_daily_digest",
    "email_initial",
    "email_reminder",
    "email_weekly_digest",
]
_limit = "octet_length(NEW.values::text)>65536"
_fields = "        WHEN 'testing' THEN ARRAY['testing_recipient'] END;"
_shape = "    RETURN NEW;\nEND $$;"
for marker in (_limit, _fields, _shape):
    if BACKWARD.count(marker) != 1:
        raise RuntimeError("Frozen setup content admission is unavailable.")
FORWARD = BACKWARD.replace(
    _limit,
    "octet_length(NEW.values::text)>(CASE WHEN NEW.step='campaign' "
    "OR left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' "
    "THEN 1048576 ELSE 65536 END)",
).replace(
    _fields,
    "        WHEN 'testing' THEN ARRAY['testing_recipient']\n"
    "        ELSE ARRAY['id','values'] END;",
)
_proof = """
    IF left(NEW.step,5)='page_' OR left(NEW.step,6)='email_' THEN
        IF NEW.values<>'{"id":null,"values":null}'::jsonb AND (
            NEW.values->'values'->>'campaign_id' IS DISTINCT FROM attempt.id::text
            OR NEW.values->'values'->>'kind' IS DISTINCT FROM split_part(NEW.step,'_',1)
            OR NEW.values->'values'->>'slot' IS DISTINCT FROM
                substring(NEW.step from position('_' in NEW.step)+1)
        ) THEN
            RAISE EXCEPTION 'Setup content belongs to its original campaign and slot'
                USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM public.stewardship_setup_draft_section draft
            JOIN public.stewardship_setup_source_result result
                ON result.id::text=draft.values->>'source_result'
            JOIN public.stewardship_setup_source_exchange exchange
                ON exchange.id=result.exchange_id AND exchange.attempt_id=attempt.id
                AND exchange.scrubbed_at IS NULL
            JOIN public.stewardship_task_run task ON task.id=exchange.task_id
                AND task.state='succeeded' AND task.root_id=attempt.source_task_id
                AND task.fence=exchange.task_fence
            JOIN public.stewardship_setup_sealed_credential credential
                ON credential.id=exchange.credential_id
                AND credential.scrubbed_at IS NULL
                AND credential.version=exchange.credential_version
                AND credential.fingerprint=exchange.fingerprint
            JOIN public.stewardship_source_snapshot snapshot
                ON snapshot.id=result.snapshot_id AND snapshot.state='ready'
            WHERE draft.attempt_id=attempt.id AND draft.step='campaign'
                AND draft.scrubbed_at IS NULL
        ) THEN
            RAISE EXCEPTION 'Setup content requires its validated staged campaign'
                USING ERRCODE='23514';
        END IF;
    END IF;
"""
FORWARD = FORWARD.replace(_shape, _proof + _shape)


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0072_setup_campaign_draft")]
    operations = [
        migrations.AlterField(
            model_name="setupdraftsection",
            name="step",
            field=models.CharField(max_length=48),
        ),
        migrations.RemoveConstraint(
            model_name="setupdraftsection", name="setup_public_step"
        ),
        migrations.AddConstraint(
            model_name="setupdraftsection",
            constraint=models.CheckConstraint(
                condition=models.Q(step__in=STEPS), name="setup_public_step"
            ),
        ),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
