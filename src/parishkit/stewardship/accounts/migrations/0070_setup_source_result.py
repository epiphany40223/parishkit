"""Retain exact staged-source success separately from final configuration."""

from importlib import import_module

from django.db import migrations, models

from parishkit.stewardship.storage_migrations import immutable_guard_v1

_fields = import_module(
    "parishkit.stewardship.accounts.migrations.0063_setup_configuration_journal"
)._fields

FORWARD = """
CREATE FUNCTION public.stewardship_setup_source_result_guard_v1()
RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
DECLARE exchange public.stewardship_setup_source_exchange%ROWTYPE;
BEGIN
    IF current_user<>'pk_stewardship_worker'
       OR NOT EXISTS (SELECT 1 FROM pg_locks WHERE pid=pg_backend_pid()
            AND locktype='advisory' AND classid=736220 AND objid=1 AND objsubid=2
            AND mode='ExclusiveLock' AND granted) THEN
        RAISE EXCEPTION 'Setup result requires its ordered source worker'
            USING ERRCODE='23514';
    END IF;
    SELECT * INTO exchange FROM public.stewardship_setup_source_exchange
        WHERE id=NEW.exchange_id;
    IF NOT FOUND OR exchange.replied_at IS NULL OR exchange.scrubbed_at IS NOT NULL
       OR NEW.actor_id IS DISTINCT FROM exchange.worker_id
       OR NOT public.stewardship_setup_exchange_live_v1(
            exchange.attempt_id,exchange.credential_id,exchange.credential_version,
            exchange.fingerprint,exchange.task_id,exchange.task_fence,
            exchange.worker_id,exchange.source_fence)
       OR NOT EXISTS (SELECT 1 FROM public.stewardship_source_snapshot snapshot
            JOIN public.stewardship_setup_sealed_credential candidate
                ON candidate.id=exchange.credential_id
                AND snapshot.organization_id=
                    (candidate.settings->>'organization_id')::bigint
            WHERE snapshot.id=NEW.snapshot_id AND snapshot.state='ready'
                AND snapshot.kind='full' AND snapshot.base_id IS NULL
                AND snapshot.task_id=exchange.task_id
                AND snapshot.source_fence=exchange.source_fence) THEN
        RAISE EXCEPTION 'Setup result requires its exact validated source snapshot'
            USING ERRCODE='23514';
    END IF;
    NEW.created_at=clock_timestamp();
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_setup_source_result_insert
BEFORE INSERT ON public.stewardship_setup_source_result
FOR EACH ROW EXECUTE FUNCTION public.stewardship_setup_source_result_guard_v1();
"""

BACKWARD = """
LOCK TABLE public.stewardship_setup_source_result IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM public.stewardship_setup_source_result) THEN
        RAISE EXCEPTION 'Setup source result history prevents downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_setup_source_result_insert
    ON public.stewardship_setup_source_result;
DROP FUNCTION public.stewardship_setup_source_result_guard_v1();
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_accounts", "0069_setup_exchange_guards")]
    operations = [
        migrations.CreateModel(
            name="SetupSourceResult",
            fields=_fields()
            + [
                (
                    "exchange",
                    models.OneToOneField(
                        on_delete=models.PROTECT,
                        to="stewardship_accounts.setupsourceexchange",
                    ),
                ),
                (
                    "snapshot",
                    models.OneToOneField(
                        on_delete=models.PROTECT,
                        to="stewardship_source.sourcesnapshot",
                    ),
                ),
            ],
            options={"db_table": "stewardship_setup_source_result"},
        ),
        immutable_guard_v1("stewardship_setup_source_result"),
        migrations.RunSQL(FORWARD, BACKWARD),
    ]
