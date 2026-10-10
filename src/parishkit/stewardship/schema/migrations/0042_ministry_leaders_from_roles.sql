-- Frozen forward migration file 0042 (the repository-wide file sequence):
-- Ministry leaders come from ParishSoft roster roles (#922). It is installed
-- by the Django migration that names it in FROZEN_SQL and must never change
-- once released; tests/stewardship/test_schema_migration_files.py pins its
-- digest and checks that each replaced function's copy here still equals the
-- fresh-install baseline's. A fresh install runs the baseline and the
-- earlier files and then this; the baseline already carries the replaced
-- bodies, so the install ends in the same catalog as an upgraded database.
--
-- The Administrator decided (2026-10-09) that a Ministry's leaders are the
-- people holding one of the campaign's leader roles (by default Chairperson
-- or Staff) in that Ministry in ParishSoft, with no manual Ministry
-- assignment or Chairperson confirmation in the Admin portal. This file:
--
--   adds stewardship_ministry_leader_roles_v1   the campaign's role names,
--                                                with the one default
--   adds stewardship_ministry_leader_role_key_v1
--                                                the form role names are
--                                                compared in
--   adds stewardship_ministry_leaders_v1        every current leader (the
--                                                one definition; definer,
--                                                executable by no login)
--   adds stewardship_ministry_leader_scope_v1   one user's role-derived
--                                                Ministries (definer; web,
--                                                worker and download)
--   replaces stewardship_ministry_scope_v1      Ministries now come only from
--                                                the role-derived scope; a
--                                                rule's Ministry leader role
--                                                and Ministry assignments no
--                                                longer grant anything. Same
--                                                signature, so every caller
--                                                (export authorization and
--                                                capture, follow-up, and the
--                                                report and follow-up v2
--                                                functions) follows at once.
--   replaces stewardship_portal_session_admission_v1
--                                                admits Administrator or Staff
--                                                by rule, or a leader with a
--                                                non-empty role-derived scope
--   replaces stewardship_campaign_pointer_v1    a live campaign's
--                                                ministry_leader_roles value
--                                                stays editable (one more
--                                                exempt key)
--   replaces stewardship_require_chair_receipt_v1
--                                                no longer requires a chair
--                                                reconciliation receipt: a
--                                                seeded assignment grants no
--                                                scope now
--
-- Production held no Ministry leader assignment in effect when this was
-- written (#922's probe), so nobody loses access. No table, row, credential,
-- code or link changes; the assignment and chair tables stay, unread.
-- stewardship_export_authorized_v1 is unchanged: it never admitted Ministry
-- leaders. Each replaced body is its latest definition with only the change
-- described; none of them is SECURITY DEFINER, and CREATE OR REPLACE keeps
-- their owners and grants. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The campaign's Ministry leader role names: its ministry_leader_roles value,
-- or the default when the campaign has none (#922). This is the only place
-- the default is written down; SQL and Python both read it here.
CREATE FUNCTION public.stewardship_ministry_leader_roles_v1(campaign_values jsonb)
    RETURNS jsonb LANGUAGE sql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT coalesce(campaign_values->'ministry_leader_roles',
        '["Chairperson", "Staff"]'::jsonb)
$$;

-- The form a Ministry role name is compared in (#922): every run of Unicode
-- whitespace (the characters Python's str.isspace() accepts, the no-break
-- space included) becomes one space, the ends are trimmed, and ASCII letters
-- are lowercased; nothing else changes, so no locale-dependent or Unicode
-- case folding applies. campaigns.configuration.leader_role_key is the same
-- rule in Python, and a test checks the two agree character for character.
CREATE FUNCTION public.stewardship_ministry_leader_role_key_v1(name text)
    RETURNS text LANGUAGE sql IMMUTABLE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT translate(btrim(regexp_replace(name,
        '[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+',
        ' ', 'g'), ' '),
        'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')
$$;

-- Every current Ministry leader (#922), the one definition of who leads
-- what: each (address, Ministry, Member) where an active Member, whatever
-- their Family's status, holds a current roster row on a catalog-present
-- Ministry of the current campaign (asking about Ministries, the Ministry
-- selected and not marked inactive here) whose role label is one of the
-- campaign's leader role names, and the address is a valid email on that
-- Member's ParishSoft contact record. Role labels match by
-- stewardship_ministry_leader_role_key_v1; addresses match
-- case-insensitively with no provider folding. No current campaign or
-- promoted source means no leaders.
-- Definer: it reads Member contact data no reader of a single scope needs.
-- No login may EXECUTE it; stewardship_ministry_leader_scope_v1 does.
CREATE FUNCTION public.stewardship_ministry_leaders_v1()
    RETURNS TABLE(email text, ministry_duid integer, member_duid bigint)
    LANGUAGE plpgsql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
DECLARE
    current_configuration uuid; current_values jsonb; current_snapshot uuid;
    leader_keys text[];
BEGIN
    -- The setting is read on its own first, so the query below is planned
    -- with the current snapshot as a known value. Sources keep several
    -- retained snapshots' roster rows; the query then reads only the
    -- current snapshot's, through its snapshot_id index, rather than
    -- scanning and parsing every retained row (#939 review). Nothing is
    -- cached between calls: the web runs with CONN_MAX_AGE 0, so each
    -- request plans this afresh, and the cost is paid per call.
    SELECT runtime.active_configuration_id, campaign.values, pointer.snapshot_id
      INTO current_configuration, current_values, current_snapshot
    FROM public.stewardship_system_configuration runtime
    JOIN public.stewardship_campaign_configuration campaign
        ON campaign.record_id=runtime.current_campaign_id
       AND campaign.configuration_id=runtime.active_configuration_id
    CROSS JOIN public.stewardship_source_current pointer
    WHERE pointer.singleton AND campaign.values->'modules' ? 'ministry';
    IF current_snapshot IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT public.stewardship_ministry_leader_role_key_v1(role.name))
      INTO leader_keys
    FROM jsonb_array_elements_text(
        public.stewardship_ministry_leader_roles_v1(current_values)) role(name);
    -- Each MATERIALIZED step runs once: the current roster parsed once;
    -- its few distinct labels, each compared once (the planner would
    -- otherwise push the comparison down to every roster row); and the
    -- leaders' rows, so the keyed lookups after them run for those only.
    RETURN QUERY
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
    )
    SELECT DISTINCT lower(address.value->>'value'), duid.ministry::integer,
        CASE WHEN member.source_key ~ '^[0-9]{1,18}$' THEN member.source_key::bigint END
    FROM held
    JOIN public.stewardship_snapshot_member mm
        ON mm.snapshot_id=current_snapshot AND mm.source_key=held.member_key
    JOIN public.stewardship_source_member member ON member.id=mm.payload_id
    JOIN public.stewardship_snapshot_ministry tm
        ON tm.snapshot_id=current_snapshot AND tm.source_key=held.ministry_key
    JOIN public.stewardship_source_ministry ministry ON ministry.id=tm.payload_id
    JOIN public.stewardship_snapshot_contact cm
        ON cm.snapshot_id=current_snapshot AND cm.source_key='member:' || member.source_key
    JOIN public.stewardship_source_contact contact ON contact.id=cm.payload_id
    CROSS JOIN LATERAL (SELECT member.canonical::jsonb AS member_doc,
        ministry.canonical::jsonb AS ministry_doc,
        contact.canonical::jsonb AS contact_doc) parsed
    -- Each DUID is a positive signed 32-bit key; CASE orders the cast after
    -- the shape test.
    CROSS JOIN LATERAL (SELECT CASE WHEN ministry.source_key ~ '^[0-9]{1,10}$'
        THEN ministry.source_key::bigint END AS ministry) duid
    CROSS JOIN LATERAL jsonb_array_elements(parsed.contact_doc->'emails') address(value)
    WHERE parsed.member_doc->'schema_version'='1'::jsonb
      AND parsed.contact_doc->'schema_version'='1'::jsonb
      AND parsed.ministry_doc->'schema_version'='1'::jsonb
      AND parsed.member_doc->'active'='true'::jsonb
      AND parsed.ministry_doc->'catalog_present'='true'::jsonb
      AND duid.ministry BETWEEN 1 AND 2147483647
      AND current_values->'ministry_duids' @> jsonb_build_array(duid.ministry)
      AND address.value->'valid'='true'::jsonb
      AND jsonb_typeof(address.value->'value')='string'
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_ministry_activity activity
          WHERE activity.configuration_id=current_configuration
            AND activity.organization_id=ministry.organization_id
            AND activity.ministry_duid=duid.ministry
            AND NOT activity.active);
END $$;
-- Stated again on its own, as every definer is.
ALTER FUNCTION public.stewardship_ministry_leaders_v1() SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leaders_v1() FROM PUBLIC;

-- One portal user's role-derived Ministry scope (#922): the ascending DUIDs
-- of the Ministries stewardship_ministry_leaders_v1 lists for that user's
-- current address, or [] for an unknown or disabled user or an address an
-- exact-address rule explicitly denies: an empty role set refuses
-- everything, ParishSoft leadership included. (Unticking an address's last
-- role on the Portal users page removes its rule instead, so only a
-- deliberate deny blocks.) One address that several leader Members list
-- gets the union of their Ministries, since whoever controls it can sign
-- in as any of them. Definer, so the web,
-- worker and download logins, which call it (directly, through
-- stewardship_ministry_scope_v1 or through the Admin session guard), learn
-- only a scope and read no contact data; database-grants grants EXECUTE to
-- exactly those three.
CREATE FUNCTION public.stewardship_ministry_leader_scope_v1(user_uuid uuid)
    RETURNS jsonb LANGUAGE sql STABLE SECURITY DEFINER
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT coalesce(jsonb_agg(DISTINCT leader.ministry_duid ORDER BY leader.ministry_duid),
        '[]'::jsonb)
    FROM public.stewardship_portal_user u
    JOIN public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(u.email)
    WHERE u.id=user_uuid AND NOT u.disabled
      AND NOT EXISTS (SELECT 1 FROM public.stewardship_system_configuration r
          JOIN public.stewardship_address_rule a
            ON a.configuration_id=r.active_configuration_id
          WHERE a.email=lower(u.email) AND a.roles='[]'::jsonb)
$$;
ALTER FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) SECURITY DEFINER;
REVOKE ALL ON FUNCTION public.stewardship_ministry_leader_scope_v1(uuid) FROM PUBLIC;

-- Ministries come only from the role-derived scope (#922); Administrator
-- and Staff stay operational by rule.
CREATE OR REPLACE FUNCTION public.stewardship_ministry_scope_v1(user_uuid uuid)
RETURNS jsonb LANGUAGE sql STABLE SET search_path TO pg_catalog,public,pg_temp AS $$
    WITH policy AS (
        SELECT coalesce(a.roles,d.roles,'[]'::jsonb) AS roles,
            stewardship_ministry_leader_scope_v1(u.id) AS ministries
        FROM stewardship_portal_user u CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=user_uuid AND NOT u.disabled
    )
    SELECT jsonb_build_object('capability','ministry_report',
        'operational',p.roles ?| ARRAY['administrator','staff'],
        'ministries',p.ministries)
    FROM policy p WHERE p.roles ?| ARRAY['administrator','staff']
        OR jsonb_array_length(p.ministries)>0
$$;

CREATE OR REPLACE FUNCTION public.stewardship_portal_session_admission_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- #306 M2: the SQL Admin checks trust these rows, so an Admin session
    -- cannot be minted by a web logic bug that SQL can see. The absolute
    -- limit is ADMIN_ABSOLUTE (12 hours, accounts/session_policy.py); an
    -- authority rotation copies the older row's earlier deadline. The
    -- principal must be a live PortalUser whose current address or domain
    -- rule grants Administrator or Staff (the same projection as
    -- stewardship_export_authorized_v1), or who leads at least one Ministry
    -- through a ParishSoft role (#922, stewardship_ministry_leader_scope_v1).
    -- A rule's Ministry leader role alone no longer admits anyone. Python
    -- may grant fewer roles than this, never more.
    IF NEW.expires_at > statement_timestamp() + interval '12 hours'
       OR NEW.authenticated_at > statement_timestamp()
       OR NEW.last_activity_at > statement_timestamp()
       OR NOT EXISTS (
        SELECT 1 FROM stewardship_portal_user u
        CROSS JOIN stewardship_system_configuration r
        LEFT JOIN stewardship_address_rule a ON a.configuration_id=r.active_configuration_id
            AND a.email=lower(u.email)
        LEFT JOIN stewardship_domain_rule d ON d.configuration_id=r.active_configuration_id
            AND d.domain=lower(u.hosted_domain) AND d.domain=split_part(lower(u.email),'@',2)
        WHERE u.id=NEW.principal_id AND NOT u.disabled
            AND (coalesce(a.roles,d.roles,'[]'::jsonb) ?| ARRAY['administrator','staff']
                 OR jsonb_array_length(public.stewardship_ministry_leader_scope_v1(u.id))>0))
    THEN
        RAISE EXCEPTION 'Admin session requires a current authorized principal and bounded lifetime'
            USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END $$;

-- The live-campaign structural lock exempts ministry_leader_roles too: the
-- Administrator may change which ParishSoft roles lead while it runs.
CREATE OR REPLACE FUNCTION public.stewardship_campaign_pointer_v1() RETURNS trigger
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE target uuid; candidate stewardship_campaign_configuration%ROWTYPE;
    live_state text; live_before jsonb; live_added numeric[];
BEGIN
    PERFORM pg_advisory_xact_lock(736220,1);
    IF TG_OP='INSERT' THEN
        IF NEW.current_campaign_id IS NOT NULL THEN RAISE EXCEPTION 'Bootstrap cannot select a campaign' USING ERRCODE='23514'; END IF;
        RETURN NEW;
    END IF;
    IF NEW.active_configuration_id IS NOT DISTINCT FROM OLD.active_configuration_id THEN RETURN NEW; END IF;
    IF NEW.current_campaign_id IS DISTINCT FROM OLD.current_campaign_id THEN
        RAISE EXCEPTION 'Configuration cannot replace the current pointer' USING ERRCODE='23514';
    END IF;
    target := OLD.current_campaign_id;
    IF target IS NOT NULL AND EXISTS (
        SELECT 1 FROM stewardship_campaign_configuration c WHERE c.configuration_id=NEW.active_configuration_id
        AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)
    ) THEN RAISE EXCEPTION 'Current campaign prevents successor creation' USING ERRCODE='23514'; END IF;
    IF target IS NULL THEN
        IF (SELECT count(*) FROM stewardship_campaign_configuration c
            WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id)) > 1 THEN
            RAISE EXCEPTION 'Only one successor can be created' USING ERRCODE='23514';
        END IF;
        SELECT record_id INTO target FROM stewardship_campaign_configuration c
        WHERE c.configuration_id=NEW.active_configuration_id AND NOT EXISTS (SELECT 1 FROM stewardship_campaign WHERE id=c.record_id);
        IF target IS NOT NULL AND (NEW.mode<>'testing' OR NEW.restore_review_required
            OR EXISTS (SELECT 1 FROM stewardship_campaign WHERE state NOT IN ('archived','purged'))
            OR EXISTS (SELECT 1 FROM stewardship_campaign_work_gate WHERE state IN ('preparing','running'))) THEN
            RAISE EXCEPTION 'Successor creation is not admitted' USING ERRCODE='23514';
        END IF;
    END IF;
    IF EXISTS (
        SELECT 1 FROM stewardship_campaign old_campaign
        JOIN stewardship_campaign_configuration old_c ON old_c.id=old_campaign.active_configuration_id
        LEFT JOIN stewardship_campaign_configuration new_c ON new_c.record_id=old_campaign.id AND new_c.configuration_id=NEW.active_configuration_id
        WHERE new_c.id IS NULL OR (old_campaign.id IS DISTINCT FROM target AND old_c.values IS DISTINCT FROM new_c.values)
    ) THEN RAISE EXCEPTION 'Historical campaigns cannot be removed or edited' USING ERRCODE='23514'; END IF;
    IF target IS NOT NULL THEN
        SELECT * INTO candidate FROM stewardship_campaign_configuration WHERE record_id=target AND configuration_id=NEW.active_configuration_id;
        IF NOT FOUND THEN RAISE EXCEPTION 'Current campaign cannot be removed' USING ERRCODE='23514'; END IF;
        IF NEW.restore_review_required AND EXISTS (
            SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND old_c.values IS DISTINCT FROM candidate.values
        ) THEN RAISE EXCEPTION 'Restore review holds campaign configuration changes' USING ERRCODE='23514'; END IF;
        IF OLD.current_campaign_id IS NULL AND candidate.timezone<>(SELECT timezone FROM stewardship_parish WHERE configuration_id=NEW.active_configuration_id) THEN
            RAISE EXCEPTION 'New draft must copy the parish timezone' USING ERRCODE='23514';
        END IF;
        IF EXISTS (SELECT 1 FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
            WHERE c.id=target AND c.structural_locked
              AND (old_c.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles']) IS DISTINCT FROM (candidate.values - ARRAY['name','year_label','content_versions','end_date','artwork','reminder_workgroup','ministry_duids','ministry_leader_roles'])) THEN
            RAISE EXCEPTION 'Live structural settings are locked' USING ERRCODE='23514';
        END IF;
        -- The one reviewed live structural exemption (#342): an Administrator
        -- may change a locked campaign's Ministry selections while it is still
        -- open. The value must stay canonical: a strictly ascending array of
        -- whole numbers, as the YAML schema writes it. Removing is always
        -- allowed. Every added DUID must be visible now: in the promoted
        -- catalog, not inactive in this candidate's Ministry activity, in a
        -- campaign with the Ministry module. Answers are never touched here.
        -- Nested CASEs fix the evaluation order, so a malformed value is
        -- refused, not miscast.
        SELECT c.state, old_c.values->'ministry_duids' INTO live_state, live_before
        FROM stewardship_campaign c JOIN stewardship_campaign_configuration old_c ON old_c.id=c.active_configuration_id
        WHERE c.id=target AND c.structural_locked
          AND old_c.values->'ministry_duids' IS DISTINCT FROM candidate.values->'ministry_duids';
        IF FOUND THEN
            IF live_state NOT IN ('scheduled','active')
                OR NOT coalesce(CASE WHEN jsonb_typeof(candidate.values->'ministry_duids')='array'
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)
                        WHERE jsonb_typeof(e.duid)<>'number' OR e.duid#>>'{}'!~'^[0-9]{1,19}$') THEN
                    candidate.values->'ministry_duids'=(SELECT coalesce(jsonb_agg(to_jsonb(d.duid) ORDER BY d.duid),'[]'::jsonb)
                        FROM (SELECT DISTINCT (e.duid#>>'{}')::numeric AS duid
                              FROM jsonb_array_elements(candidate.values->'ministry_duids') e(duid)) d)
                    END,false) THEN
                RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
            END IF;
            -- The value is now a canonical array of whole numbers.
            SELECT coalesce(array_agg((added.duid#>>'{}')::numeric),'{}') INTO live_added
            FROM jsonb_array_elements(candidate.values->'ministry_duids') added(duid)
            WHERE NOT coalesce(live_before @> added.duid,false);
            IF cardinality(live_added)>0 THEN
                IF NOT coalesce(candidate.values->'modules' ? 'ministry',false)
                    OR EXISTS (SELECT 1 FROM unnest(live_added) d WHERE d NOT BETWEEN 1 AND 2147483647) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
                -- A separate statement, reached only when a locked open campaign
                -- gains a Ministry: PostgreSQL checks EXECUTE on the definer
                -- stewardship_ministry_catalog_v1 whenever a statement naming it
                -- starts, whatever a CASE or AND would evaluate. Only the
                -- configuration installer, which holds no source grants, needs
                -- it; setup completion and other activations never get here.
                IF EXISTS (SELECT 1 FROM unnest(live_added) d
                    WHERE NOT EXISTS (SELECT 1 FROM stewardship_ministry_catalog_v1() present
                        WHERE present.ministry_duid=d::integer
                          AND NOT EXISTS (SELECT 1 FROM stewardship_ministry_activity activity
                              WHERE activity.configuration_id=NEW.active_configuration_id
                                AND activity.organization_id=present.organization_id
                                AND activity.ministry_duid=present.ministry_duid
                                AND NOT activity.active))) THEN
                    RAISE EXCEPTION 'Live Ministry selections can only add current active Ministries to an open campaign' USING ERRCODE='23514';
                END IF;
            END IF;
        END IF;
    END IF;
    NEW.current_campaign_id := target;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION public.stewardship_require_chair_receipt_v1(configuration uuid, snapshot uuid) RETURNS void
    LANGUAGE plpgsql
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
BEGIN
    -- Ministry leaders come from ParishSoft roles (#922): Chairperson-seeded
    -- assignments and their overlays grant no scope, so a promotion or
    -- activation no longer needs a chair reconciliation receipt. The
    -- signature stays for its two callers; the tables stay, unread.
    RETURN;
END;
$$;

-- Refuse to commit unless everything above is installed as declared.
DO $check$
BEGIN
    -- The two definers: their signatures, SECURITY DEFINER, the fixed
    -- search_path, no EXECUTE for PUBLIC, and the rules that matter.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND NOT has_function_privilege('public', p.oid, 'EXECUTE')
          AND ((p.proname='stewardship_ministry_leaders_v1'
                AND pg_get_function_identity_arguments(p.oid)=''
                AND p.prosrc LIKE '%public.stewardship_ministry_leader_roles_v1(current_values)%'
                AND p.prosrc LIKE '%public.stewardship_ministry_leader_role_key_v1(labels.label)=ANY(leader_keys)%'
                AND p.prosrc LIKE '%WHERE rm.snapshot_id=current_snapshot%'
                AND p.prosrc LIKE '%roster.doc->''current''=''true''::jsonb%'
                AND p.prosrc LIKE '%parsed.member_doc->''active''=''true''::jsonb%'
                AND p.prosrc LIKE '%address.value->''valid''=''true''::jsonb%'
                AND p.prosrc LIKE '%current_values->''ministry_duids'' @> jsonb_build_array(duid.ministry)%'
                AND p.prosrc LIKE '%NOT activity.active%')
            OR (p.proname='stewardship_ministry_leader_scope_v1'
                AND pg_get_function_identity_arguments(p.oid)='user_uuid uuid'
                AND p.prorettype='jsonb'::regtype
                AND p.prosrc LIKE '%public.stewardship_ministry_leaders_v1() leader ON leader.email=lower(u.email)%'
                AND p.prosrc LIKE '%a.roles=''[]''::jsonb%'))
       )<>2 THEN
        RAISE EXCEPTION 'Migration 0042: the Ministry leader functions are not installed as declared';
    END IF;
    -- The default role names, from the one accessor, and the comparison
    -- form: whitespace runs (no-break spaces too) collapse, the ends trim
    -- and only ASCII letters lowercase.
    IF public.stewardship_ministry_leader_role_key_v1(
           E' \u00a0Team\t \u2003ONE\u00a0Lead\u00c9r ')
           IS DISTINCT FROM E'team one lead\u00c9r'
       OR public.stewardship_ministry_leader_roles_v1('{}'::jsonb)
           IS DISTINCT FROM '["Chairperson", "Staff"]'::jsonb
       OR public.stewardship_ministry_leader_roles_v1('{"ministry_leader_roles": ["Lead"]}'::jsonb)
           IS DISTINCT FROM '["Lead"]'::jsonb THEN
        RAISE EXCEPTION 'Migration 0042: the Ministry leader role helpers are not installed as declared';
    END IF;
    -- The replaced and new invoker functions: not SECURITY DEFINER, the fixed
    -- search_path, and their new rules.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND ((p.proname='stewardship_ministry_leader_roles_v1'
                AND pg_get_function_identity_arguments(p.oid)='campaign_values jsonb'
                AND p.provolatile='i')
            OR (p.proname='stewardship_ministry_leader_role_key_v1'
                AND pg_get_function_identity_arguments(p.oid)='name text'
                AND p.provolatile='i')
            OR (p.proname='stewardship_ministry_scope_v1'
                AND pg_get_function_identity_arguments(p.oid)='user_uuid uuid'
                AND p.prosrc LIKE '%stewardship_ministry_leader_scope_v1(u.id) AS ministries%'
                AND p.prosrc NOT LIKE '%stewardship_ministry_assignment%'
                AND p.prosrc NOT LIKE '%''ministry_leader''%')
            OR (p.proname='stewardship_portal_session_admission_v1'
                AND p.prosrc LIKE '%jsonb_array_length(public.stewardship_ministry_leader_scope_v1(u.id))>0%'
                AND p.prosrc NOT LIKE '%''ministry_leader''%')
            OR (p.proname='stewardship_campaign_pointer_v1'
                AND regexp_count(p.prosrc,
                    '''reminder_workgroup'',''ministry_duids'',''ministry_leader_roles''')=2)
            OR (p.proname='stewardship_require_chair_receipt_v1'
                AND pg_get_function_identity_arguments(p.oid)='configuration uuid, snapshot uuid'
                AND p.prosrc NOT LIKE '%RAISE%'))
       )<>6 THEN
        RAISE EXCEPTION 'Migration 0042: a replaced function is not installed as declared';
    END IF;
END
$check$;
