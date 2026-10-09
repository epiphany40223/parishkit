-- Frozen forward migration file 0035 (the repository-wide file sequence):
-- the paused-delivery resume preview counts Reminder WorkGroup Families'
-- reminders as skipped (#866). This file is installed by its Django
-- migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest, and
-- tests/stewardship/database/test_delivery_workgroup_recovery_postgresql.py
-- checks that its view text still equals the fresh-install baseline's
-- (schema/delivery_recovery.sql). A fresh install runs the baseline, the
-- earlier files and then this; the baseline already carries this view, so
-- the install ends in the same catalog as an upgraded database.
--
-- stewardship_delivery_family_recovery is the private plan behind the resume
-- preview's Family counts (stewardship_delivery_family_recovery_summary, the
-- one view the Web role reads) and behind what confirming the resume writes
-- (stewardship_delivery_recover_families_v1). Planning skips a Reminder
-- WorkGroup Family's reminders (#861), but this view did not, so the preview
-- counted them as to be emailed or coalesced into the invitation, and the
-- send-time check then skipped them. The view now gives those reminder rows
-- the reason workgroup_excluded (after family_responded, before
-- no_deliverable_recipient, as planning orders them), so they count and are
-- written as skipped. Membership is the current snapshot's recorded read,
-- and only while its name is the campaign's reminder_workgroup setting, as
-- source.workgroups reads it. The Family's invitation is unaffected.
--
-- The column list is unchanged, so CREATE OR REPLACE VIEW keeps the owner,
-- the grants and the summary view built on it; the DO block checks both
-- views' grants against the ones recorded before the replacement. The
-- recovery function and the summary are not replaced: both read this view
-- at run time. The summary's fingerprint changes with the new reason, so a
-- resume previewed before this upgrade must be previewed again. No existing
-- row changes. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- Both views' grants before the replacement, compared in the DO block. A
-- transaction-local setting holds them: the migration login has no TEMP
-- privilege.
SELECT set_config('stewardship.migration_0035_acl', (
    SELECT string_agg(c.relname||'='||coalesce(c.relacl::text,'default'),';' ORDER BY c.relname)
    FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname='public' AND c.relname IN ('stewardship_delivery_family_recovery',
        'stewardship_delivery_family_recovery_summary')), true);

CREATE OR REPLACE VIEW public.stewardship_delivery_family_recovery AS
WITH scope AS (
    -- workgroup_duids: the campaign's Reminder WorkGroup members (#861, #866),
    -- read as source.workgroups.excluded_duids reads them. The current
    -- snapshot's recorded read counts only while its name is the campaign's
    -- setting, so a changed or cleared name stops applying at once.
    SELECT c.id,c.production_cycle,p.ends_at,(
        SELECT s.cursor->'load'->'reminder_workgroup'->'family_duids'
        FROM public.stewardship_source_current sc
        JOIN public.stewardship_source_snapshot s ON s.id=sc.snapshot_id
        WHERE coalesce(p.values->>'reminder_workgroup','')<>''
            AND jsonb_typeof(s.cursor->'load'->'reminder_workgroup'->'name')='string'
            AND s.cursor->'load'->'reminder_workgroup'->>'name'=p.values->>'reminder_workgroup'
            AND jsonb_typeof(s.cursor->'load'->'reminder_workgroup'->'family_duids')='array'
    ) AS workgroup_duids
    FROM public.stewardship_system_configuration r
    JOIN public.stewardship_campaign c ON c.id=r.current_campaign_id
    JOIN public.stewardship_campaign_configuration p ON p.id=c.active_configuration_id
    WHERE r.mode='production' AND c.delivery_paused
), inputs AS (
    SELECT s.id AS campaign_id,f.id AS family_id,f.version AS family_version,
        f.source_generation,f.active,f.email_eligible,f.email_deliverable,
        f.effective_submission_id,s.ends_at,
        d.id AS definition_id,d.version AS definition_version,d.kind,
        d.current_revision_id AS revision_id,v.due_at,
        o.id AS occurrence_id,o.version AS occurrence_version,
        coalesce(o.state,'pending') AS state,o.outbox_id,
        coalesce(w.blocking,false) AS uncertain,
        w.task_versions,w.outbox_version,
        EXISTS(SELECT 1 FROM public.stewardship_schedule_definition initial
            JOIN public.stewardship_restore_delivery_hold h ON h.definition_id=initial.id
            WHERE initial.campaign_id=s.id AND initial.kind='initial'
                AND h.mode='production' AND h.target='family:'||f.id::text
                AND h.slot='once' AND h.state='unreviewed') AS initial_unreviewed
    FROM scope s
    JOIN public.stewardship_family_campaign f ON f.campaign_id=s.id
    JOIN public.stewardship_schedule_definition d ON d.campaign_id=s.id
        AND d.kind IN ('initial','reminder') AND d.current_revision_id IS NOT NULL
    JOIN public.stewardship_schedule_revision v ON v.id=d.current_revision_id
        AND v.due_at<=public.stewardship_campaign_now_v1()
    LEFT JOIN LATERAL (
        SELECT current.* FROM public.stewardship_schedule_occurrence current
        WHERE current.revision_id=d.current_revision_id AND current.mode='production'
            AND current.target='family:'||f.id::text AND current.slot='once'
            AND current.production_cycle=s.production_cycle
        ORDER BY current.recovery_generation DESC LIMIT 1
    ) o ON true
    LEFT JOIN public.stewardship_schedule_work_row w ON w.id=o.id
    WHERE NOT EXISTS(SELECT 1 FROM public.stewardship_schedule_fulfillment covered
        WHERE covered.definition_id=d.id AND covered.mode='production'
            AND covered.target='family:'||f.id::text AND covered.slot='once')
      AND NOT EXISTS(SELECT 1 FROM public.stewardship_restore_delivery_hold h
        WHERE h.definition_id=d.id AND h.mode='production'
            AND h.target='family:'||f.id::text AND h.slot='once'
            AND h.state IN ('unreviewed','assumed_delivered'))
      AND (o.id IS NOT NULL OR (f.active AND f.email_eligible
          AND f.effective_submission_id IS NULL))
), groups AS (
    SELECT inputs.*,
        bool_or(uncertain OR state='delivery_unknown') OVER family AS group_uncertain,
        bool_or(kind='initial' AND state IN ('failed','skipped','coalesced'))
            OVER family AS initial_unfulfilled,
        first_value(definition_id) OVER (
            PARTITION BY family_id ORDER BY
                CASE WHEN state IN ('pending','running') THEN 0 ELSE 1 END,
                CASE kind WHEN 'initial' THEN 0 ELSE 1 END,
                due_at DESC,definition_id DESC,occurrence_id DESC
        ) AS selected_definition
    FROM inputs WINDOW family AS (PARTITION BY family_id)
), reasons AS (
    SELECT groups.*,CASE
        WHEN group_uncertain THEN 'delivery_unresolved'
        WHEN public.stewardship_campaign_now_v1()>=ends_at THEN 'campaign_closed'
        WHEN NOT active OR NOT email_eligible THEN 'family_ineligible'
        WHEN effective_submission_id IS NOT NULL THEN 'family_responded'
        -- A Reminder WorkGroup Family's reminders are skipped, as planning
        -- skips them (plan_family, same precedence). Only its reminder rows:
        -- its due invitation is still selected, and its reminders no longer
        -- coalesce into it.
        WHEN kind='reminder' AND EXISTS(SELECT 1 FROM scope s
            JOIN public.stewardship_family_campaign wf ON wf.id=groups.family_id
            WHERE s.id=groups.campaign_id
                AND s.workgroup_duids @> jsonb_build_array(wf.family_duid))
            THEN 'workgroup_excluded'
        WHEN NOT email_deliverable THEN 'no_deliverable_recipient'
        WHEN initial_unreviewed OR initial_unfulfilled THEN 'initial_unfulfilled'
        ELSE '' END AS reason
    FROM groups
) SELECT reasons.*,CASE
    WHEN state NOT IN ('pending','running','delivery_unknown') THEN 'unchanged'
    WHEN reason='delivery_unresolved' THEN 'blocked'
    WHEN reason='initial_unfulfilled' THEN 'deferred'
    WHEN reason<>'' THEN 'skipped'
    WHEN definition_id=selected_definition THEN 'selected'
    ELSE 'coalesced' END AS disposition
FROM reasons;

-- Refuse to commit unless the new view is installed, the summary and the
-- recovery function still read it, and nothing lost its rights.
DO $check$
BEGIN
    IF position('workgroup_excluded' IN pg_get_viewdef('public.stewardship_delivery_family_recovery'::regclass))=0
        OR position('workgroup_duids' IN pg_get_viewdef('public.stewardship_delivery_family_recovery'::regclass))=0 THEN
        RAISE EXCEPTION 'stewardship_delivery_family_recovery was not replaced with the #866 view';
    END IF;
    IF NOT EXISTS(SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid=d.objid
        WHERE r.ev_class='public.stewardship_delivery_family_recovery_summary'::regclass
            AND d.refobjid='public.stewardship_delivery_family_recovery'::regclass) THEN
        RAISE EXCEPTION 'stewardship_delivery_family_recovery_summary no longer reads the plan';
    END IF;
    IF NOT (SELECT prosecdef FROM pg_proc
        WHERE oid='public.stewardship_delivery_recover_families_v1(uuid)'::regprocedure) THEN
        RAISE EXCEPTION 'stewardship_delivery_recover_families_v1 lost SECURITY DEFINER';
    END IF;
    IF current_setting('stewardship.migration_0035_acl', true) IS DISTINCT FROM (
        SELECT string_agg(c.relname||'='||coalesce(c.relacl::text,'default'),';' ORDER BY c.relname)
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname IN ('stewardship_delivery_family_recovery',
            'stewardship_delivery_family_recovery_summary')) THEN
        RAISE EXCEPTION 'The Family recovery views lost or changed their grants';
    END IF;
END
$check$;
