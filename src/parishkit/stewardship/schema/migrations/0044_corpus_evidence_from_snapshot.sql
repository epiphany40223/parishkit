-- Frozen forward migration file 0044 (the repository-wide file sequence):
-- the source corpus evidence checks start from the snapshot they check
-- (#629). It is installed by the Django migration that names it in
-- FROZEN_SQL and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copy of the replaced function still equals the fresh-install
-- baseline's (schema/functions.sql). A fresh install
-- runs the baseline and the earlier files and then this; the baseline
-- already carries the replaced body, so the install ends in the same catalog
-- as an upgraded database.
--
-- The snapshot guard calls stewardship_source_corpus_evidence when a
-- snapshot becomes 'ready' (a full refresh) or 'unchanged' (a quick update
-- that changed nothing, every quarter hour). Its seven relationship checks
-- and two contact/address owner checks were planned from the payload side:
-- every retained payload of the kind, then each payload's membership rows in
-- every snapshot sharing it, filtered to this snapshot at the end. An
-- unchanged snapshot has no rows, so no check could stop early and each read
-- the whole membership table: about 6.9 M rows and 9.4 s of a core in
-- Production, under the global work-order lock (#629's 2026-10-10
-- findings). This file replaces only that function:
--
--   replaces stewardship_source_corpus_evidence(uuid)
--       skips a check whose child collection has no rows in this snapshot
--       (the counts the function already computes), and starts each check
--       from this snapshot's references: a MATERIALIZED CTE reads its rows by
--       the snapshot_id index and their payloads by primary key, and only
--       then are the parents looked up. The work is bounded by one corpus,
--       not by retention.
--
-- The refusals are identical: a check is skipped only when its EXISTS has no
-- row to find, and a rewritten check reads the same joins with the same
-- conditions. The returned counts and digest are unchanged. The body is
-- functions.sql's previous one with only those two changes. The function is
-- not SECURITY DEFINER and sets no search_path in the baseline, and
-- CREATE OR REPLACE keeps its owner and grants, so nothing is re-altered. No
-- table, row or index changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function, exactly as the fresh-install functions.sql defines it.
CREATE OR REPLACE FUNCTION public.stewardship_source_corpus_evidence(snapshot_id uuid) RETURNS jsonb
    LANGUAGE plpgsql
    AS $_$
DECLARE kind text;
        items jsonb;
        item_count bigint;
        manifest jsonb := '{}'::jsonb;
        counts jsonb := '{}'::jsonb;
        relation text[];
        invalid boolean;
BEGIN
    FOREACH kind IN ARRAY ARRAY['family','member','contact','address','ministry',
        'roster','fund','pledge','contribution'] LOOP
        EXECUTE format('SELECT COALESCE(jsonb_object_agg(m.source_key,p.digest),'
            '''{}''::jsonb),count(*) FROM %I m JOIN %I p ON p.id=m.payload_id '
            'WHERE m.snapshot_id=$1', 'stewardship_snapshot_'||kind,
            'stewardship_source_'||kind) INTO items,item_count USING snapshot_id;
        manifest := manifest || jsonb_build_object(kind,items);
        counts := counts || jsonb_build_object(kind,item_count);
    END LOOP;
    FOREACH relation SLICE 1 IN ARRAY ARRAY[
        ['member','family_key','family'], ['roster','member_key','member'],
        ['roster','ministry_key','ministry'], ['pledge','family_key','family'],
        ['pledge','fund_key','fund'], ['contribution','family_key','family'],
        ['contribution','fund_key','fund']
    ] LOOP
        -- An empty collection has nothing to resolve, so an unchanged quick
        -- update (no rows at all) runs no check (#629).
        CONTINUE WHEN counts->>relation[1]='0';
        -- Read this snapshot's references first (its snapshot_id index, then
        -- each payload by primary key), materialized so the planner cannot
        -- walk every retained payload and each one's rows in every snapshot
        -- sharing it instead (#629).
        EXECUTE format('WITH c AS MATERIALIZED (SELECT p.%4$I AS parent_key '
            'FROM %1$I m JOIN %2$I p ON p.id=m.payload_id WHERE m.snapshot_id=$1) '
            'SELECT EXISTS(SELECT 1 FROM c WHERE NOT EXISTS(SELECT 1 FROM %3$I parent '
            'WHERE parent.snapshot_id=$1 AND parent.source_key=c.parent_key))',
            'stewardship_snapshot_'||relation[1], 'stewardship_source_'||relation[1],
            'stewardship_snapshot_'||relation[3], relation[2])
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has unresolved relationships'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    FOREACH kind IN ARRAY ARRAY['contact','address'] LOOP
        CONTINUE WHEN counts->>kind='0';
        EXECUTE format('WITH c AS MATERIALIZED (SELECT p.owner_kind,p.owner_key '
            'FROM %I m JOIN %I p ON p.id=m.payload_id WHERE m.snapshot_id=$1) '
            'SELECT EXISTS(SELECT 1 FROM c '
            'LEFT JOIN stewardship_snapshot_family f ON f.snapshot_id=$1 '
            'AND f.source_key=c.owner_key AND c.owner_kind=''family'' '
            'LEFT JOIN stewardship_snapshot_member b ON b.snapshot_id=$1 '
            'AND b.source_key=c.owner_key AND c.owner_kind=''member'' '
            'WHERE f.id IS NULL AND b.id IS NULL)',
            'stewardship_snapshot_'||kind,'stewardship_source_'||kind)
            INTO invalid USING snapshot_id;
        IF invalid THEN
            RAISE EXCEPTION 'Source corpus has an unresolved contact owner'
                USING ERRCODE='23514';
        END IF;
    END LOOP;
    RETURN jsonb_build_object('counts', counts, 'digest', encode(sha256(convert_to(
        stewardship_source_canonical(manifest),'UTF8')),'hex'));
END;
$_$;

-- Refuse to commit unless the new body is installed with the baseline's
-- attributes: invoker's rights, no fixed search_path, and every check
-- skipped when empty and started from the snapshot.
DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_source_corpus_evidence'
          AND pg_get_function_identity_arguments(p.oid)='snapshot_id uuid'
          AND p.prorettype='jsonb'::regtype
          AND NOT p.prosecdef AND p.proconfig IS NULL
          AND p.prosrc LIKE '%CONTINUE WHEN counts->>relation[1]=''0'';%'
          AND p.prosrc LIKE '%CONTINUE WHEN counts->>kind=''0'';%'
          AND regexp_count(p.prosrc, 'WITH c AS MATERIALIZED \(SELECT p\.')=2
          AND p.prosrc NOT LIKE '%WHERE m.snapshot_id=$1 AND%')<>1 THEN
        RAISE EXCEPTION 'Migration 0044: stewardship_source_corpus_evidence is not installed as declared';
    END IF;
END
$check$;
