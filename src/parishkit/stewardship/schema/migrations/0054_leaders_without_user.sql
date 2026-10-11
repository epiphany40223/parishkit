-- Frozen forward migration file 0054 (the repository-wide file sequence):
-- the Users page's ParishSoft Ministry leaders without a user (#952, slice
-- 6). It is installed by the Django migration that names it in FROZEN_SQL
-- and must never change once released; tests/stewardship/
-- test_schema_migration_files.py pins its digest. It replaces nothing, so
-- the fresh-install baseline is unchanged: a fresh install runs the
-- baseline, the earlier files and then this.
--
-- Below the users table, the Users page lists the people ParishSoft counts
-- as Ministry leaders who have no user yet, so the Administrator can import
-- them (admin-portal specification, "ParishSoft Ministry leaders without a
-- user"). The web login has no grant on Member, contact or roster records,
-- and migration 0042's stewardship_ministry_leaders_v1 is EXECUTE-able by
-- no login. This file:
--
--   adds stewardship_leaders_without_user_v1(text, uuid)
--                                            the leaders table's rows and
--                                            the count of leaders with no
--                                            usable email, for a live
--                                            Administrator session, audited
--                                            (definer; web)
--
-- The caller passes its Admin session's key and its correlation ID, as
-- 0053's stewardship_member_names_for_v1 does, and the definer refuses
-- (SQLSTATE 42501) under the same rule: the session is live (not revoked,
-- before its deadline, within the 60-minute idle limit, and begun after the
-- latest offline Admin-access recovery) and its user is an Administrator
-- now. It takes no address or other argument, so it is not an oracle: every
-- call answers the same rows for the current data, and each records one
-- user_leaders_listed audit event (the Administrator and the number of
-- rows, never an address). Against a compromised web login that can pass a
-- live Administrator's session key (#995), what it discloses is exactly
-- what the page shows the Administrator.
--
-- Who is a leader copies 0042's definition: an active Member of an active
-- Family (a non-parishioner Family included; an inactive Member never)
-- holding one of the current campaign's Ministry leader roles on a current
-- roster row of a catalog-present Ministry that the campaign selects, asks
-- about and has not marked inactive. Only the email step differs: 0042
-- needs a valid address to give a scope, while this lists every leader
-- Member, so one with no valid address is counted rather than lost.
-- tests/stewardship/database/test_leader_import_postgresql.py proves the two
-- agree on every leader address.
--
-- The answer is {"leaders": [...], "unreachable": n}. Each leader row is one
-- valid address (trimmed and lowercased, as sign-in matches) that some
-- leader Member lists and that is not a user in the active configuration:
-- its email, the Name and Member DUID of the Member that the Name default
-- order puts first (a head of household first, then by name, then by DUID;
-- every leader's Family is active, so the active-Family step of that order
-- never differs here), the name "Last, first" as 0053 builds it, and the
-- ascending DUIDs of every Ministry its leader Members lead (the union, as
-- their sign-in scope would be). Rows come in Name order. "unreachable"
-- counts the leader Members whose contact record lists no valid address.
-- Web gets EXECUTE through `database-grants`
-- (runtime_grants.LEADERS_WITHOUT_USER_FUNCTION). Reversing needs its own
-- forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

CREATE FUNCTION public.stewardship_leaders_without_user_v1(admin_session text, correlation uuid)
    RETURNS jsonb
    LANGUAGE plpgsql VOLATILE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    actor uuid;
    current_configuration uuid; current_values jsonb; current_snapshot uuid;
    leader_keys text[];
    answer jsonb := jsonb_build_object('leaders', '[]'::jsonb, 'unreachable', 0);
    event uuid := gen_random_uuid();
BEGIN
    -- Who asks: a live Admin session whose user is an Administrator now,
    -- exactly as stewardship_member_names_for_v1 (0053) decides it.
    SELECT login.principal_id INTO actor
    FROM public.stewardship_portal_session login
    WHERE login.session_id=admin_session AND login.revoked_at IS NULL
      AND login.expires_at>clock_timestamp()
      AND login.last_activity_at>clock_timestamp()-interval '60 minutes'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_admin_revocation r
                      WHERE r.created_at>login.created_at);
    IF actor IS NULL OR correlation IS NULL
       OR NOT public.stewardship_export_authorized_v1(actor, true) THEN
        RAISE EXCEPTION 'Leader list requires a live Administrator session'
            USING ERRCODE='42501';
    END IF;
    -- The current campaign, its configuration and the promoted snapshot, as
    -- 0042 reads them; no campaign asking about Ministries, or no promoted
    -- source, means no leaders.
    SELECT runtime.active_configuration_id, campaign.values, pointer.snapshot_id
      INTO current_configuration, current_values, current_snapshot
    FROM public.stewardship_system_configuration runtime
    JOIN public.stewardship_campaign_configuration campaign
        ON campaign.record_id=runtime.current_campaign_id
       AND campaign.configuration_id=runtime.active_configuration_id
    CROSS JOIN public.stewardship_source_current pointer
    WHERE pointer.singleton AND campaign.values->'modules' ? 'ministry';
    IF current_snapshot IS NOT NULL THEN
        SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
          INTO leader_keys
        FROM jsonb_array_elements_text(
            public.stewardship_ministry_leader_roles_v1(current_values)) role(name);
        WITH roster AS MATERIALIZED (
            SELECT payload.member_key, payload.ministry_key, payload.canonical::jsonb AS doc
            FROM public.stewardship_snapshot_roster rm
            JOIN public.stewardship_source_roster payload ON payload.id=rm.payload_id
            WHERE rm.snapshot_id=current_snapshot
        ), labels AS MATERIALIZED (
            SELECT DISTINCT roster.doc->>'ministryRoleName' AS label FROM roster
        ), leader_labels AS MATERIALIZED (
            SELECT labels.label FROM labels
            WHERE public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)
        ), held AS MATERIALIZED (
            SELECT roster.member_key, roster.ministry_key
            FROM roster JOIN leader_labels ON leader_labels.label=roster.doc->>'ministryRoleName'
            WHERE roster.doc->'schema_version'='1'::jsonb
              AND roster.doc->'current'='true'::jsonb
        ), led AS MATERIALIZED (
            -- 0042's joins and conditions, with the contact record optional.
            SELECT member.source_key AS member_key, duid.ministry AS ministry_duid,
                parsed.member_doc, parsed.family_doc,
                CASE WHEN contact.canonical::jsonb->'schema_version'='1'::jsonb
                    THEN contact.canonical::jsonb->'emails' END AS emails
            FROM held
            JOIN public.stewardship_snapshot_member mm
                ON mm.snapshot_id=current_snapshot AND mm.source_key=held.member_key
            JOIN public.stewardship_source_member member ON member.id=mm.payload_id
            JOIN public.stewardship_snapshot_family fm
                ON fm.snapshot_id=current_snapshot AND fm.source_key=member.family_key
            JOIN public.stewardship_source_family family ON family.id=fm.payload_id
            JOIN public.stewardship_snapshot_ministry tm
                ON tm.snapshot_id=current_snapshot AND tm.source_key=held.ministry_key
            JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
            LEFT JOIN public.stewardship_snapshot_contact cm
                ON cm.snapshot_id=current_snapshot AND cm.source_key='member:' || member.source_key
            LEFT JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
            CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
                family.canonical::jsonb AS family_doc,
                ministry.canonical::jsonb AS ministry_doc) parsed
            CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
                THEN ministry.source_key::bigint END AS ministry) duid
            WHERE parsed.member_doc->'schema_version'='1'::jsonb
              AND parsed.family_doc->'schema_version'='1'::jsonb
              AND parsed.ministry_doc->'schema_version'='1'::jsonb
              AND parsed.member_doc->'active'='true'::jsonb
              AND parsed.family_doc->'active'='true'::jsonb
              AND parsed.ministry_doc->'catalog_present'='true'::jsonb
              AND duid.ministry BETWEEN 1 AND 2147483647
              AND current_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
              AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_activity activity
                  WHERE activity.configuration_id=current_configuration
                    AND activity.organization_id=ministry.organization_id
                    AND activity.ministry_duid=duid.ministry
                    AND NOT activity.active)
        ), people AS MATERIALIZED (
            -- One row per leader Member: its "Last, first" name, whether it
            -- heads its household, its DUID and its listed addresses.
            SELECT DISTINCT ON (led.member_key) led.member_key,
                concat_ws(', ',
                    nullif(btrim(led.member_doc->>'lastName'), ''),
                    nullif(btrim(led.member_doc->>'firstName'), '')) AS name,
                EXISTS (SELECT 1 FROM jsonb_array_elements_text(
                            CASE WHEN jsonb_typeof(led.family_doc->'active_head_duids')='array'
                                THEN led.family_doc->'active_head_duids' END) head(duid)
                        WHERE head.duid=led.member_key) AS head,
                CASE WHEN led.member_key ~ '^[0-9]{1,18}$'
                    THEN led.member_key::bigint END AS duid,
                CASE WHEN jsonb_typeof(led.emails)='array' THEN led.emails
                    ELSE '[]'::jsonb END AS emails
            FROM led ORDER BY led.member_key
        ), addresses AS MATERIALIZED (
            -- Each valid address a leader Member lists, as sign-in matches it.
            SELECT DISTINCT people.member_key, lower(btrim(listed.value->>'value')) AS email
            FROM people CROSS JOIN LATERAL jsonb_array_elements(people.emails) listed(value)
            WHERE listed.value->'valid'='true'::jsonb
              AND jsonb_typeof(listed.value->'value')='string'
              AND length(btrim(listed.value->>'value')) BETWEEN 3 AND 254
        ), unused AS MATERIALIZED (
            -- Only addresses that are not a user in the active configuration.
            SELECT addresses.member_key, addresses.email FROM addresses
            WHERE NOT EXISTS (SELECT 1 FROM public.stewardship_address_rule rule
                WHERE rule.configuration_id=current_configuration
                  AND rule.email=addresses.email)
        ), ranked AS (
            SELECT unused.email, people.name, people.duid, row_number() OVER (
                PARTITION BY unused.email
                ORDER BY people.name='', people.head DESC, people.name,
                    people.duid NULLS LAST) AS position
            FROM unused JOIN people ON people.member_key=unused.member_key
        ), answered AS (
            SELECT ranked.email, ranked.name, ranked.duid,
                (SELECT jsonb_agg(DISTINCT led.ministry_duid ORDER BY led.ministry_duid)
                 FROM unused JOIN led ON led.member_key=unused.member_key
                 WHERE unused.email=ranked.email) AS ministries
            FROM ranked WHERE ranked.position=1
        )
        SELECT jsonb_build_object(
            'leaders', coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'email', answered.email, 'name', answered.name,
                    'member_duid', answered.duid, 'ministries', answered.ministries)
                ORDER BY answered.name, answered.duid NULLS LAST, answered.email)
                FROM answered), '[]'::jsonb),
            'unreachable', (SELECT count(*) FROM people
                WHERE NOT EXISTS (SELECT 1 FROM addresses
                                  WHERE addresses.member_key=people.member_key)))
          INTO answer;
    END IF;
    -- The trace: who listed and how many rows came back, never an address.
    INSERT INTO public.stewardship_audit_event(
        id,created_at,actor_id,correlation_id,event_type,subject_id,ownership_scope)
    VALUES(event,clock_timestamp(),actor,correlation,'user_leaders_listed',NULL,'deployment');
    INSERT INTO public.stewardship_audit_context(
        id,created_at,actor_id,correlation_id,event_id,actor_kind,schema,context)
    VALUES(gen_random_uuid(),clock_timestamp(),actor,correlation,event,'portal_user','action',
        jsonb_build_object('outcome','succeeded',
            'count',jsonb_array_length(answer->'leaders')));
    RETURN answer;
END
$$;
ALTER FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_leaders_without_user_v1(text, uuid) FROM PUBLIC;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The one definer: SECURITY DEFINER, the fixed search_path, no EXECUTE
    -- for PUBLIC, its two arguments and jsonb answer, the session, recovery
    -- and Administrator checks, 0042's Member and Family flags, only
    -- addresses that are no user, the audit event, and no other overload.
    IF NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
            WHERE n.nspname='public' AND p.proname='stewardship_leaders_without_user_v1'
              AND p.prosecdef AND NOT p.proretset AND p.provolatile='v'
              AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
              AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
              AND pg_get_function_identity_arguments(p.oid)
                  ='admin_session text, correlation uuid'
              AND pg_get_function_result(p.oid)='jsonb'
              AND p.prosrc LIKE '%WHERE login.session_id=admin_session AND login.revoked_at IS NULL%'
              AND p.prosrc LIKE '%WHERE r.created_at>login.created_at);%'
              AND p.prosrc LIKE '%OR NOT public.stewardship_export_authorized_v1(actor, true) THEN%'
              AND p.prosrc LIKE '%AND parsed.member_doc->''active''=''true''::jsonb%'
              AND p.prosrc LIKE '%AND parsed.family_doc->''active''=''true''::jsonb%'
              AND p.prosrc LIKE '%WHERE rule.configuration_id=current_configuration%'
              AND p.prosrc LIKE '%VALUES(event,clock_timestamp(),actor,correlation,''user_leaders_listed'',NULL,''deployment'');%')
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_leaders_without_user_v1')<>1 THEN
        RAISE EXCEPTION 'Migration 0054: the leaders definer is not installed as declared';
    END IF;
END
$check$;
