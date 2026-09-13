"""Bind closed progress phases to claims/progress and immutable event history."""

from django.db import migrations


def rewrite_history(schema_editor, *, reverse=False):
    """Modify only the reviewed history columns, preserving its existing audit work."""
    replacements = (
        (
            "progress_current, progress_total)",
            "progress_current, progress_total, phase)",
        ),
        (
            "NEW.progress_current, NEW.progress_total);",
            "NEW.progress_current, NEW.progress_total, NEW.phase);",
        ),
    )
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_get_functiondef('stewardship_task_history_v1()'::regprocedure)"
        )
        definition = cursor.fetchone()[0]
        for original, replacement in replacements:
            if reverse:
                original, replacement = replacement, original
            if definition.count(original) != 1:
                raise RuntimeError("Task history function differs from its migration.")
            definition = definition.replace(original, replacement)
        cursor.execute(definition)


def install(apps, schema_editor):
    """Phase updates supplement, rather than replace, existing state/fence guards."""
    if schema_editor.connection.vendor != "postgresql":
        return
    rewrite_history(schema_editor)
    schema_editor.execute(
        """
CREATE FUNCTION stewardship_task_phase_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF TG_OP='INSERT' THEN
        IF NEW.phase<>'unspecified' THEN
            RAISE EXCEPTION 'New task phase must be unspecified' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.action='claim' THEN
        IF NEW.phase<>'starting' THEN
            RAISE EXCEPTION 'Task claim must reset its phase' USING ERRCODE='23514';
        END IF;
    ELSIF NEW.phase IS DISTINCT FROM OLD.phase THEN
        IF NEW.action<>'progress' OR NEW.phase='unspecified' THEN
            RAISE EXCEPTION 'Task phase requires progress' USING ERRCODE='23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_task_phase_v1 BEFORE INSERT OR UPDATE
ON stewardship_task_run FOR EACH ROW EXECUTE FUNCTION stewardship_task_phase_v1();

CREATE FUNCTION stewardship_task_event_phase_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path=pg_catalog,public,pg_temp AS $$
BEGIN
    IF NEW.phase IS DISTINCT FROM (
        SELECT phase FROM stewardship_task_run WHERE id=NEW.run_id
    ) THEN
        RAISE EXCEPTION 'Task phase history must match its execution'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER stewardship_task_event_phase_v1 BEFORE INSERT
ON stewardship_task_event FOR EACH ROW
EXECUTE FUNCTION stewardship_task_event_phase_v1();
"""
    )


def remove(apps, schema_editor):
    """Refuse populated downgrades before removing any phase/history protection."""
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
LOCK TABLE stewardship_task_run,stewardship_task_event IN ACCESS EXCLUSIVE MODE;
DO $$ BEGIN
    IF EXISTS(SELECT 1 FROM stewardship_task_run)
       OR EXISTS(SELECT 1 FROM stewardship_task_event) THEN
        RAISE EXCEPTION 'Task history prevents phase schema downgrade'
            USING ERRCODE='23514';
    END IF;
END $$;
DROP TRIGGER stewardship_task_event_phase_v1 ON stewardship_task_event;
DROP FUNCTION stewardship_task_event_phase_v1();
DROP TRIGGER stewardship_task_phase_v1 ON stewardship_task_run;
DROP FUNCTION stewardship_task_phase_v1();
"""
    )
    rewrite_history(schema_editor, reverse=True)


class Migration(migrations.Migration):
    dependencies = [("stewardship_jobs", "0003_task_progress_phases")]
    operations = [migrations.RunPython(install, remove)]
