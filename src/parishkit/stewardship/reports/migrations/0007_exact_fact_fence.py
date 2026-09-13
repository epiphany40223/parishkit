"""Bind liveness to the argument fence, never the identically named table column."""

from importlib import import_module

from django.db import migrations

_old = import_module("parishkit.stewardship.reports.migrations.0002_fact_guards")
REVERSE = _old.FORWARD.split("CREATE FUNCTION stewardship_fact_disposable", 1)[
    0
].replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
FORWARD = """
CREATE OR REPLACE FUNCTION public.stewardship_fact_live(
    task uuid, fence bigint, worker uuid)
RETURNS boolean LANGUAGE sql VOLATILE
SET search_path=pg_catalog,public,pg_temp AS $$
    SELECT EXISTS(SELECT 1 FROM public.stewardship_task_run t WHERE t.id=$1
        AND t.state='running' AND t.fence=$2 AND t.worker_id=$3
        AND t.lease_expires_at > clock_timestamp());
$$;
"""


class Migration(migrations.Migration):
    dependencies = [("stewardship_reports", "0006_compaction_evidence_guard")]
    operations = [migrations.RunSQL(FORWARD, REVERSE)]
