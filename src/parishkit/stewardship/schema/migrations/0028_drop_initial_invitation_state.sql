-- Frozen forward migration file 0028 (the repository-wide file sequence):
-- drop the unused stewardship_family_campaign.initial_invitation_state
-- column (#472). It must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest. A later
-- change gets its own numbered file. A fresh install runs the baseline
-- (whose tables.sql no longer has the column), the earlier files and then
-- this, and ends in the same catalog as an upgraded database.
--
-- Nothing ever read or updated the column; only the ORM's model default
-- filled it when a row was created. Every Family row read "not_sent", even
-- for Families whose invitation was delivered, because invitation state is
-- kept in the schedule occurrences and outbox messages. No function, view,
-- trigger, grant or export names it. Rows are created only through the ORM,
-- whose model no longer lists it.
--
-- IF EXISTS because a fresh install's baseline already lacks the column. No
-- CASCADE: an unexpected dependent object fails the migration instead of
-- being dropped with it. The scripted upgrade stops every application
-- service before migrating, so no running process still lists the column.
-- DROP COLUMN only marks the column dropped in the catalog (no table
-- rewrite), so the brief exclusive lock it takes is held for milliseconds.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

ALTER TABLE public.stewardship_family_campaign
    DROP COLUMN IF EXISTS initial_invitation_state;

-- Refuse to commit unless the column is gone and the table is still there.
DO $check$
BEGIN
    IF to_regclass('public.stewardship_family_campaign') IS NULL THEN
        RAISE EXCEPTION 'stewardship_family_campaign is missing';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_attribute
        WHERE attrelid='public.stewardship_family_campaign'::regclass
          AND attname='initial_invitation_state'
          AND attnum>0 AND NOT attisdropped) THEN
        RAISE EXCEPTION 'initial_invitation_state was not dropped';
    END IF;
END
$check$;
