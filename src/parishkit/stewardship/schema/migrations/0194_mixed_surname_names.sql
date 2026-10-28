-- Frozen forward migration file 0194 (the repository-wide file sequence,
-- number assigned centrally): clearer staff-list names when a Family's
-- heads have different surnames (#2320). This file is installed by its
-- Django migration in the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and
-- checks that its copies of the baseline functions equal the fresh-install
-- baseline's. A fresh install runs the baseline, the earlier files and then
-- this; the baseline already carries the replaced directory selection, so
-- the install ends in the same catalog as an upgraded database.
--
-- family_names.family_heads_name lists a Family for staff: "Squyres, Tracy
-- and Jeff". A head of another surname was shown in full in head order, so
-- "Kelly, Linda Thieneman and Maureen" read as one person. Now, when the
-- heads have more than one surname, the surnames lead, separated by " / "
-- (the Family surname first, then the others in head order), and after a
-- comma each surname's first names follow in the same order, also separated
-- by " / ": "Kelly / Thieneman, Maureen and Bob / Linda". Last names group
-- ignoring letter case (casefold, the first spelling kept); heads without a
-- last name follow in full; a surname whose heads have no first names
-- leaves no empty part. When no head shares the Family surname only the
-- heads' own surnames lead ("Lee / Fox, Ann / Bo"). When every head shares
-- it, the name is unchanged.
--
-- Created, only here:
--   stewardship_name_series_v1(text[])  family_names.name_series: "A",
--                                       "A and B", "A, B and C"; blank or
--                                       NULL parts skipped
-- Replaced functions, each with its signature, attributes and grants kept:
--   stewardship_family_head_part_v1   (0045) one head's part is now its
--                                     first and last name as a JSON array
--                                     text, '["Ann","Lee"]', so the whole
--                                     name can group heads by surname; ''
--                                     for an inactive or nameless head, as
--                                     before. Its surname argument is no
--                                     longer read; it stays so no caller
--                                     changes.
--   stewardship_family_heads_name_v1  (0045) builds the name above from
--                                     those parts; every caller passes
--                                     stewardship_family_head_part_v1's
--                                     parts, so the financial and
--                                     Additional information selections and
--                                     stewardship_family_display_name_v1
--                                     change with it
--   stewardship_directory_report_v1   (the baseline; 0094 last replaced it)
--   stewardship_directory_report_v2   (migration-owned; 0094) each built the
--                                     name inline; now each encodes its
--                                     heads' parts the same way and calls
--                                     stewardship_family_heads_name_v1, so
--                                     search and sort match the page
-- The directory selections are copied whole from their newest definitions
-- with only that change. None of these functions is SECURITY DEFINER,
-- before or after, and none has a REVOKE, so there is no ALTER FUNCTION or
-- REVOKE to repeat. The DO block below checks the attributes, settings,
-- grants recorded before the replacement and the new bodies, names sample
-- Families as Python does, and refuses each older body. Reversing needs its
-- own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants and settings before the replacement (with
-- how many definitions there are), compared in the DO block. Settings are
-- compared in sorted order. A transaction-local setting holds them, not a
-- temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0194_acl', (
    SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
            ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
        ORDER BY p.proname),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN ('stewardship_family_head_part_v1',
        'stewardship_family_heads_name_v1','stewardship_directory_report_v1',
        'stewardship_directory_report_v2')), true);

-- family_names.name_series: the non-blank parts joined naturally ("A", "A
-- and B", "A, B and C"); '' when none is left. NULL parts are skipped too.
CREATE FUNCTION public.stewardship_name_series_v1(parts text[])
    RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT CASE WHEN c.n<3 THEN array_to_string(a.p,' and ')
        ELSE array_to_string(a.p[1:c.n-1],', ')||' and '||a.p[c.n] END
    FROM (SELECT coalesce(array_agg(u.part ORDER BY u.o),'{}'::text[])
        FROM unnest(parts) WITH ORDINALITY u(part,o) WHERE u.part<>'') a(p)
    CROSS JOIN LATERAL (SELECT cardinality(a.p)) c(n)
$$;

-- One head's part of family_heads_name: its stripped first and last name
-- (a non-string value counts as blank, as Python's _text does) as the text
-- of a JSON array, '["Ann","Lee"]'; '' for a head that is not active or has
-- neither name, which stewardship_family_heads_name_v1 skips. surname is
-- not read (the grouping needs every head, so it happens there).
CREATE OR REPLACE FUNCTION public.stewardship_family_head_part_v1(head jsonb, surname text)
    RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT CASE WHEN head->'active' IS DISTINCT FROM 'true'::jsonb THEN ''
        WHEN n.first='' AND n.last='' THEN ''
        ELSE jsonb_build_array(n.first,n.last)::text END
    FROM (SELECT
        coalesce(stewardship_name_strip_v1(CASE WHEN jsonb_typeof(head->'firstName')='string'
            THEN head->>'firstName' END),''),
        coalesce(stewardship_name_strip_v1(CASE WHEN jsonb_typeof(head->'lastName')='string'
            THEN head->>'lastName' END),'')) n(first,last)
$$;

-- family_names.family_heads_name from the heads' parts in head DUID order
-- (stewardship_family_head_part_v1's JSON arrays; '' or NULL parts are
-- skipped). Heads with a last name group under it, compared by casefold
-- (Python's str.casefold), each group keeping its first spelling and
-- placed at its first head. When no group but the Family surname's is
-- left, the name is unchanged: the surname, then every head's first name
-- in head order ("Squyres, Tracy and Jeff"; a head without a last name has
-- only a first name here). Otherwise the surnames lead, " / " between them
-- (the Family surname first when a head has it, then the other groups),
-- then a comma and each group's first names (name_series) in the same
-- order, then the first names of heads without a last name, " / " between
-- the non-blank parts: "Kelly / Thieneman, Maureen and Bob / Linda". No
-- parts at all gives the surname alone.
CREATE OR REPLACE FUNCTION public.stewardship_family_heads_name_v1(surname text, parts text[])
    RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    WITH heads AS (
        SELECT u.o,coalesce(j.p->>0,'') AS first,coalesce(j.p->>1,'') AS last,
            CASE WHEN coalesce(j.p->>1,'')<>''
                THEN casefold((j.p->>1) COLLATE pg_catalog.pg_unicode_fast) END AS key
        FROM unnest(parts) WITH ORDINALITY u(part,o)
        CROSS JOIN LATERAL (SELECT u.part::jsonb) j(p)
        WHERE u.part<>''
    ), groups AS (
        SELECT h.key,min(h.o) AS o,(array_agg(h.last ORDER BY h.o))[1] AS last,
            stewardship_name_series_v1(array_agg(h.first ORDER BY h.o)) AS firsts,
            coalesce(h.key=casefold(surname COLLATE pg_catalog.pg_unicode_fast),false) AS own
        FROM heads h WHERE h.key IS NOT NULL GROUP BY h.key
    ), shown AS (
        SELECT stewardship_name_series_v1(ARRAY(SELECT first FROM heads ORDER BY o)) AS same,
            array_to_string(ARRAY(SELECT CASE WHEN own THEN surname ELSE last END
                FROM groups ORDER BY own DESC,o),' / ') AS surnames,
            array_to_string(ARRAY(SELECT nullif(x.part,'') FROM (
                SELECT firsts AS part,NOT own AS rank,o FROM groups
                UNION ALL SELECT first,NULL,o FROM heads WHERE key IS NULL) x
                ORDER BY x.rank NULLS LAST,x.o),' / ') AS names
    )
    SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM groups WHERE NOT own) THEN
            CASE WHEN s.same='' THEN surname ELSE surname||', '||s.same END
        WHEN s.names='' THEN s.surnames
        ELSE s.surnames||', '||s.names END
    FROM shown s
$$;

-- stewardship_directory_report_v1: copied from its newest definition (the
-- baseline, directory_reports.sql); only the Family name changed.
CREATE OR REPLACE FUNCTION public.stewardship_directory_report_v1(
    campaign_uuid uuid, parameters jsonb, page_number integer DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation cost about 2.3 s per call at 2,500 Families and saved
-- nothing: the selection itself runs in well under a second.
SET jit TO off
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','postal','exact','family_id']
       OR parameters-ARRAY['filters','postal','exact','family_id']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'postal') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'exact') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'family_id') NOT IN ('null','string')
       OR (parameters->>'family_id' IS NOT NULL AND
           parameters->>'family_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (parameters->'exact'='false'::jsonb AND parameters->>'family_id' IS NOT NULL)
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','reason','phone','response','sort']
       -- reach is optional: captures made before it existed omit it (= any).
       OR f-ARRAY['search','reason','phone','response','sort','reach']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'reason' NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable')
       OR f->>'phone' NOT IN ('any','yes','no')
       OR f->>'response' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('name','name_desc','duid')
       OR coalesce(f->>'reach','any') NOT IN ('any','email','mail','neither')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
    THEN RAISE EXCEPTION 'Invalid directory report parameters' USING ERRCODE='23514'; END IF;
    -- BEGIN DIRECTORY SELECTION
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.id,x.name,s.id AS source_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,s.organization_id
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
), options AS (
    SELECT f AS f
), name_trim AS (
    -- Every character Python's str.strip() removes (str.isspace), so names
    -- trimmed here match family_names.py exactly: search and sort use the
    -- same string the page shows, even with tabs or no-break spaces.
    SELECT E' \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000'::text AS ws
), family_source AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_family m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'portal_eligible'='true'::jsonb
), members AS NOT MATERIALIZED (
    SELECT p.source_key,p.family_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_member m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'active'='true'::jsonb
), contacts AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_contact m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_contact p ON p.id=m.payload_id
), addresses AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb->'fields' AS fields
    FROM source s JOIN stewardship_snapshot_address m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_address p ON p.id=m.payload_id
), head_emails AS MATERIALIZED (
    -- Every head's email entries in one set-based pass. Per-Family
    -- subqueries repeated the contacts lookup once for every Family.
    SELECT f.source_key,e.value AS email
    FROM family_source f
    CROSS JOIN LATERAL jsonb_array_elements_text(f.value->'active_head_duids') h(head)
    JOIN contacts c ON c.source_key='member:'||h.head
    CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e
), email_counts AS MATERIALIZED (
    SELECT x.source_key,count(*) AS address_count,
        count(*) FILTER (WHERE x.email->'valid'='true'::jsonb) AS eligible,
        count(*) FILTER (WHERE x.email->'valid'='true'::jsonb AND NOT EXISTS (
            SELECT 1 FROM stewardship_recipient_refusal r
            WHERE r.organization_id=s.organization_id
              AND r.family_duid=x.source_key::bigint
              AND r.address=x.email->>'value' AND NOT EXISTS (
                  SELECT 1 FROM stewardship_recipient_resolution z
                  WHERE z.refusal_id=r.id)
        )) AS deliverable
    FROM head_emails x CROSS JOIN source s
    GROUP BY x.source_key
), base AS MATERIALIZED (
    SELECT f.source_key,f.source_key::bigint AS family_duid,i.id AS family_id,
        coalesce(nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),''),
            nullif(btrim(f.value->>'mailingName',(SELECT ws FROM name_trim)),''),
            nullif(concat_ws(' ',nullif(btrim(f.value->>'firstName',(SELECT ws FROM name_trim)),''),
                nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),'')),''),
            -- A listed Family is in the data; a blank name is a gap (#1216).
            'Name not recorded in ParishSoft') AS family_name,
        f.value->'active_head_duids' AS head_duids,
        concat_ws(' ',f.value->>'firstName',f.value->>'lastName') AS search_name,
        coalesce(f.value->>'envelopeNumber','') AS envelope_number,
        jsonb_array_length(f.value->'active_head_duids') AS head_count,
        coalesce(ec.address_count,0) AS address_count,
        coalesce(ec.eligible,0)>0 AS email_eligible,
        coalesce(ec.deliverable,0)>0 AS email_deliverable,
        EXISTS(SELECT 1 FROM stewardship_submission r WHERE r.family_id=i.id
            AND r.campaign_id=campaign_uuid AND r.mode='live') AS responded,
        -- A usable mailing address: a street line and a city, plus a state or
        -- a postal code. The postal mail-merge export includes only these.
        EXISTS(SELECT 1 FROM addresses a
            WHERE a.source_key='family:'||f.source_key||':primary'
              AND nullif(btrim(a.fields->>'primaryAddress1'),'') IS NOT NULL
              AND nullif(btrim(a.fields->>'primaryCity'),'') IS NOT NULL
              AND (nullif(btrim(a.fields->>'primaryState'),'') IS NOT NULL
                   OR nullif(btrim(a.fields->>'primaryPostalCode'),'') IS NOT NULL))
            AS mailable
    FROM family_source f CROSS JOIN source s
    LEFT JOIN stewardship_family_campaign i
        ON i.campaign_id=s.id AND i.family_duid=f.source_key::bigint
    LEFT JOIN email_counts ec ON ec.source_key=f.source_key
), head_names AS MATERIALIZED (
    -- Each head's first and last name for the Family as shown
    -- (family_names.family_heads_name), encoded as
    -- stewardship_family_head_part_v1 encodes them: a JSON array
    -- [first,last], '' for a nameless head. One set-based join over every
    -- head: a per-Family subquery rescanned every Member for every Family.
    SELECT b.source_key,array_agg(p.part ORDER BY h.head::bigint)
        FILTER (WHERE p.part<>'') AS parts
    FROM base b
    CROSS JOIN LATERAL jsonb_array_elements_text(b.head_duids) h(head)
    JOIN members m ON m.source_key=h.head
    CROSS JOIN LATERAL (SELECT btrim(coalesce(m.value->>'firstName',''),(SELECT ws FROM name_trim)),
        btrim(coalesce(m.value->>'lastName',''),(SELECT ws FROM name_trim))) t(first,last)
    CROSS JOIN LATERAL (SELECT CASE WHEN t.first='' AND t.last='' THEN ''
        ELSE jsonb_build_array(t.first,t.last)::text END) p(part)
    GROUP BY b.source_key
), rows AS MATERIALIZED (
    SELECT b.*,CASE WHEN email_deliverable THEN 'deliverable'
        WHEN head_count=0 THEN 'no_head' WHEN address_count=0 THEN 'no_address'
        WHEN NOT email_eligible THEN 'invalid_address'
        ELSE 'provider_refused' END AS reason,
        -- The Family as shown (family_names.family_heads_name): "Squyres,
        -- Tracy and Jeff", or "Kelly / Thieneman, Maureen / Linda" when the
        -- heads have other surnames (#2320). Search matches it and
        -- same-surname Families sort by it; the page computes it again for
        -- display.
        stewardship_family_heads_name_v1(family_name,n.parts) AS display_name
    FROM base b LEFT JOIN head_names n ON n.source_key=b.source_key
), member_matches AS MATERIALIZED (
    -- Families with an active Member whose name contains the search (#664):
    -- first and last name, or nickname and last name. One set-based pass
    -- over the snapshot's Members, never a per-Family subquery, and none at
    -- all for an empty search. The matched Member is never returned.
    SELECT DISTINCT m.family_key FROM members m CROSS JOIN options o
    WHERE o.f->>'search'<>''
      AND (position(lower(o.f->>'search') IN lower(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')))>0
          OR position(lower(o.f->>'search') IN lower(concat_ws(' ',m.value->>'nickName',m.value->>'lastName')))>0)
), filtered AS MATERIALIZED (
    -- 'postal' selects the mail-merge columns only; it does not narrow the
    -- rows, so mailing details cover exactly the filtered Families (#202).
    SELECT r.* FROM rows r CROSS JOIN options o
    WHERE (NOT (parameters->>'exact')::boolean OR family_id=(parameters->>'family_id')::uuid)
      AND (o.f->>'reason'='any' OR reason=o.f->>'reason')
      AND (o.f->>'phone'='any' OR (
          EXISTS(SELECT 1 FROM contacts c WHERE c.source_key='family:'||r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          OR EXISTS(SELECT 1 FROM members m JOIN contacts c
              ON c.source_key='member:'||m.source_key WHERE m.family_key=r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          )=(o.f->>'phone'='yes'))
      AND (o.f->>'response'='any' OR responded=(o.f->>'response'='yes'))
      AND CASE coalesce(o.f->>'reach','any')
          WHEN 'email' THEN email_deliverable
          WHEN 'mail' THEN NOT email_deliverable AND mailable
          WHEN 'neither' THEN NOT email_deliverable AND NOT mailable
          ELSE true END
      AND (o.f->>'search'='' OR position(lower(o.f->>'search') IN lower(display_name))>0
          OR position(lower(o.f->>'search') IN lower(search_name))>0
          OR position(o.f->>'search' IN family_duid::text)>0
          OR position(o.f->>'search' IN envelope_number)>0
          OR r.source_key IN (SELECT family_key FROM member_matches)
          OR EXISTS(SELECT 1 FROM addresses a
              CROSS JOIN LATERAL jsonb_each_text(a.fields) v
              WHERE a.source_key='family:'||r.source_key||':primary'
              AND position(lower(o.f->>'search') IN lower(v.value))>0))
), ordered AS (
    -- Surname first, then the whole shown name, then the DUID.
    SELECT r.*,row_number() OVER (ORDER BY
        CASE WHEN o.f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN o.f->>'sort'='name' THEN lower(display_name) END,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(family_name) END DESC,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(display_name) END DESC,
        family_duid) AS ordinal
    FROM filtered r CROSS JOIN options o
), page AS MATERIALIZED (
    SELECT * FROM ordered ORDER BY ordinal LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE 50 END OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*50 END
), page_heads AS MATERIALIZED (
    -- The shown page's heads in one set-based join; a per-row subquery
    -- rescanned every Member for each of the page's rows.
    SELECT p.source_key,jsonb_agg(jsonb_build_object('duid',h.head,
            'name',concat_ws(' ',nullif(n.first,''),nullif(n.last,'')),
            'first',n.first,'last',n.last)
            ORDER BY h.head::bigint) AS heads
    FROM page p
    CROSS JOIN LATERAL jsonb_array_elements_text(p.head_duids) h(head)
    JOIN members m ON m.source_key=h.head
    CROSS JOIN LATERAL (SELECT btrim(coalesce(m.value->>'firstName',''),(SELECT ws FROM name_trim)),
        btrim(coalesce(m.value->>'lastName',''),(SELECT ws FROM name_trim))) n(first,last)
    GROUP BY p.source_key
), details AS (
    -- Only the selected page constructs display-only private contact JSON.
    SELECT p.*,f.value->>'envelopeNumber' AS envelope,
        coalesce(ph.heads,'[]'::jsonb) AS heads,
        coalesce((SELECT jsonb_agg(jsonb_build_object('owner',v.owner,'kind',phone.key,
            'value',phone.value) ORDER BY v.owner,phone.key,phone.value)
            FROM (
                SELECT 'Family' AS owner,c.value FROM contacts c
                WHERE c.source_key='family:'||p.source_key
                UNION ALL
                SELECT btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')),
                    c.value FROM members m
                    JOIN contacts c ON c.source_key='member:'||m.source_key
                WHERE m.family_key=p.source_key
            ) v CROSS JOIN LATERAL jsonb_each_text(v.value->'phones') phone),
            '[]'::jsonb) AS phones,
        coalesce((SELECT a.fields FROM addresses a
            WHERE a.source_key='family:'||p.source_key||':primary'),
            '{}'::jsonb) AS address
    FROM page p JOIN family_source f ON f.source_key=p.source_key
    LEFT JOIN page_heads ph ON ph.source_key=p.source_key
)
SELECT jsonb_build_object('metadata',to_jsonb(s),
    'total',(SELECT count(*) FROM filtered),
    'active_total',(SELECT count(*) FROM rows),
    'postal_total',(SELECT count(*) FROM rows WHERE NOT email_deliverable),
    'unreachable_total',(SELECT count(*) FROM rows
        WHERE NOT email_deliverable AND NOT mailable),
    'rows',coalesce((SELECT jsonb_agg(
        to_jsonb(p)-ARRAY['ordinal','head_count','address_count','search_name','envelope_number','source_key','display_name','head_duids']
        ORDER BY ordinal) FROM details p),'[]'::jsonb)) INTO answer FROM source s;
    -- END DIRECTORY SELECTION
    RETURN answer;
END $$;

-- stewardship_directory_report_v2: copied from its newest definition (0094);
-- only the Family name changed.
CREATE OR REPLACE FUNCTION public.stewardship_directory_report_v2(
    campaign_uuid uuid, parameters jsonb, page_number integer DEFAULT NULL
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation cost about 2.3 s per call at 2,500 Families and saved
-- nothing: the selection itself runs in well under a second.
SET jit TO off
SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
    report_mode text:=parameters->>'mode'; epoch_uuid uuid;
    counted timestamptz:=statement_timestamp(); funnel_needed boolean;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','postal','exact','family_id']
       OR parameters-ARRAY['filters','postal','exact','family_id','responses','mode','epoch']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'postal') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'exact') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(coalesce(parameters->'responses','false'::jsonb)) IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(coalesce(parameters->'mode','"production"'::jsonb)) IS DISTINCT FROM 'string'
       OR coalesce(report_mode,'production') NOT IN ('production','testing')
       OR jsonb_typeof(coalesce(parameters->'epoch','null'::jsonb)) NOT IN ('null','string')
       OR (parameters->>'epoch' IS NOT NULL AND
           parameters->>'epoch' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (coalesce(report_mode,'production')='testing')<>(parameters->>'epoch' IS NOT NULL)
       OR jsonb_typeof(parameters->'family_id') NOT IN ('null','string')
       OR (parameters->>'family_id' IS NOT NULL AND
           parameters->>'family_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (parameters->'exact'='false'::jsonb AND parameters->>'family_id' IS NOT NULL)
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','reason','phone','response','sort']
       -- reach and check are optional (= any).
       OR f-ARRAY['search','reason','phone','response','sort','reach','check']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'reason' NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable')
       OR f->>'phone' NOT IN ('any','yes','no')
       OR f->>'response' NOT IN ('any','yes','no','submitted','more-than-once','started',
           'progressed','opened-only','never-opened','link-followed','link-not-followed',
           'not-invited','not-submitted')
       OR f->>'sort' NOT IN ('name','name_desc','duid','duid_desc','invited','invited_desc','link','link_desc',
           'opened','opened_desc','progressed','progressed_desc','submitted','submitted_desc',
           'last','last_desc','submissions','submissions_desc')
       OR coalesce(f->>'reach','any') NOT IN ('any','email','mail','neither')
       OR coalesce(f->>'check','any') NOT IN ('any','anything','mailing-name','envelope')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
    THEN RAISE EXCEPTION 'Invalid directory report parameters' USING ERRCODE='23514'; END IF;
    report_mode:=coalesce(report_mode,'production');
    epoch_uuid:=(parameters->>'epoch')::uuid;
    -- The funnel is read only when a filter, a column or the order uses it.
    funnel_needed:=f->>'response'<>'any' OR f->>'sort' NOT IN ('name','name_desc','duid','duid_desc')
        OR coalesce((parameters->>'responses')::boolean,false);
    -- BEGIN DIRECTORY SELECTION
WITH selected AS MATERIALIZED (
    SELECT c.id,cc.name,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.id,x.name,s.id AS source_id,s.generation AS source_generation,
        s.promoted_at AS source_as_of,s.organization_id
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    WHERE s.state='promoted' AND s.compacted_at IS NULL
), options AS (
    SELECT f AS f
), name_trim AS (
    -- Every character Python's str.strip() removes (str.isspace), so names
    -- trimmed here match family_names.py exactly: search and sort use the
    -- same string the page shows, even with tabs or no-break spaces.
    SELECT E' \t\n\x0b\x0c\r\x1c\x1d\x1e\x1f\u0085                 　'::text AS ws
), funnel AS MATERIALIZED (
    -- The Response dashboard's own per-Family rows at the instant counted.
    -- A false funnel_needed is a one-time filter: the function never runs.
    SELECT r.* FROM stewardship_family_response_v1(campaign_uuid,report_mode,epoch_uuid,counted) r
    WHERE funnel_needed
), family_source AS MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_family m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'portal_eligible'='true'::jsonb
), inactive_source AS MATERIALIZED (
    -- Under a Response filter that follows a dashboard tile, the campaign's
    -- Families the current data no longer lists as active and registered,
    -- so each list keeps its dashboard count. The current data's record
    -- when it still has one, else an empty object (no name, heads or
    -- envelope). The active-only choices (and v1's yes and no) skip it.
    SELECT u.family_duid::text AS source_key,coalesce(p.canonical::jsonb,'{}'::jsonb) AS value
    FROM funnel u CROSS JOIN source s CROSS JOIN options o
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=s.source_id AND m.source_key=u.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
    WHERE o.f->>'response' NOT IN ('any','yes','no','not-invited','not-submitted')
      AND NOT EXISTS(SELECT 1 FROM family_source x WHERE x.source_key=u.family_duid::text)
), listed_source AS MATERIALIZED (
    SELECT source_key,value,true AS active FROM family_source
    UNION ALL
    SELECT source_key,value,false FROM inactive_source
), members AS NOT MATERIALIZED (
    SELECT p.source_key,p.family_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_member m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_member p ON p.id=m.payload_id
    WHERE p.canonical::jsonb->'active'='true'::jsonb
), contacts AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb AS value
    FROM source s JOIN stewardship_snapshot_contact m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_contact p ON p.id=m.payload_id
), addresses AS NOT MATERIALIZED (
    SELECT m.source_key,p.canonical::jsonb->'fields' AS fields
    FROM source s JOIN stewardship_snapshot_address m ON m.snapshot_id=s.source_id
    JOIN stewardship_source_address p ON p.id=m.payload_id
), head_emails AS MATERIALIZED (
    -- Every head's email entries in one set-based pass. Per-Family
    -- subqueries repeated the contacts lookup once for every Family.
    SELECT f.source_key,e.value AS email
    FROM family_source f
    CROSS JOIN LATERAL jsonb_array_elements_text(f.value->'active_head_duids') h(head)
    JOIN contacts c ON c.source_key='member:'||h.head
    CROSS JOIN LATERAL jsonb_array_elements(c.value->'emails') e
), email_counts AS MATERIALIZED (
    SELECT x.source_key,count(*) AS address_count,
        count(*) FILTER (WHERE x.email->'valid'='true'::jsonb) AS eligible,
        count(*) FILTER (WHERE x.email->'valid'='true'::jsonb AND NOT EXISTS (
            SELECT 1 FROM stewardship_recipient_refusal r
            WHERE r.organization_id=s.organization_id
              AND r.family_duid=x.source_key::bigint
              AND r.address=x.email->>'value' AND NOT EXISTS (
                  SELECT 1 FROM stewardship_recipient_resolution z
                  WHERE z.refusal_id=r.id)
        )) AS deliverable
    FROM head_emails x CROSS JOIN source s
    GROUP BY x.source_key
), base AS MATERIALIZED (
    -- An inactive Family has no email or postal reach: campaign mail is
    -- not sent to it. Its funnel instants are the dashboard's, with the
    -- implied ones (a submission implies the form was opened and its
    -- first step passed; progress implies the form was opened).
    SELECT f.source_key,f.source_key::bigint AS family_duid,i.id AS family_id,f.active,
        CASE WHEN f.value<>'{}'::jsonb THEN
        coalesce(nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),''),
            nullif(btrim(f.value->>'mailingName',(SELECT ws FROM name_trim)),''),
            nullif(concat_ws(' ',nullif(btrim(f.value->>'firstName',(SELECT ws FROM name_trim)),''),
                nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),'')),''),
            -- A blank name is a gap in the data (#1216).
            'Name not recorded in ParishSoft') END AS family_name,
        coalesce(f.value->'active_head_duids','[]'::jsonb) AS head_duids,
        concat_ws(' ',f.value->>'firstName',f.value->>'lastName') AS search_name,
        coalesce(f.value->>'envelopeNumber','') AS envelope_number,
        coalesce(jsonb_array_length(f.value->'active_head_duids'),0) AS head_count,
        coalesce(ec.address_count,0) AS address_count,
        f.active AND coalesce(ec.eligible,0)>0 AS email_eligible,
        f.active AND coalesce(ec.deliverable,0)>0 AS email_deliverable,
        EXISTS(SELECT 1 FROM stewardship_submission r WHERE r.family_id=i.id
            AND r.campaign_id=campaign_uuid AND r.mode='live') AS responded,
        -- A usable mailing address: a street line and a city, plus a state or
        -- a postal code. The postal mail-merge export includes only these.
        f.active AND EXISTS(SELECT 1 FROM addresses a
            WHERE a.source_key='family:'||f.source_key||':primary'
              AND nullif(btrim(a.fields->>'primaryAddress1'),'') IS NOT NULL
              AND nullif(btrim(a.fields->>'primaryCity'),'') IS NOT NULL
              AND (nullif(btrim(a.fields->>'primaryState'),'') IS NOT NULL
                   OR nullif(btrim(a.fields->>'primaryPostalCode'),'') IS NOT NULL))
            AS mailable,
        -- The two launch-day ParishSoft data problems, as the response
        -- lists judged them (source.snapshot_names): a mailing name that is
        -- missing or only whitespace, and an envelope number of exactly 0.
        f.active AND (jsonb_typeof(f.value->'mailingName') IS DISTINCT FROM 'string'
            OR btrim(f.value->>'mailingName',(SELECT ws FROM name_trim))='') AS mailing_name_blank,
        f.active AND jsonb_typeof(f.value->'envelopeNumber')='number'
            AND f.value->>'envelopeNumber'='0' AS envelope_zero,
        u.invited_at,u.link_at,
        least(u.form_at,u.progress_at,u.submitted_at) AS form_opened_at,
        least(u.progress_at,u.submitted_at) AS progressed_at,
        u.submitted_at,u.last_submitted_at,coalesce(u.submissions,0) AS submissions
    FROM listed_source f CROSS JOIN source s
    LEFT JOIN stewardship_family_campaign i
        ON i.campaign_id=s.id AND i.family_duid=f.source_key::bigint
    LEFT JOIN email_counts ec ON ec.source_key=f.source_key
    LEFT JOIN funnel u ON u.family_id=i.id
), head_names AS MATERIALIZED (
    -- Each head's first and last name for the Family as shown
    -- (family_names.family_heads_name), encoded as
    -- stewardship_family_head_part_v1 encodes them: a JSON array
    -- [first,last], '' for a nameless head. One set-based join over every
    -- head: a per-Family subquery rescanned every Member for every Family.
    SELECT b.source_key,array_agg(p.part ORDER BY h.head::bigint)
        FILTER (WHERE p.part<>'') AS parts
    FROM base b
    CROSS JOIN LATERAL jsonb_array_elements_text(b.head_duids) h(head)
    JOIN members m ON m.source_key=h.head
    CROSS JOIN LATERAL (SELECT btrim(coalesce(m.value->>'firstName',''),(SELECT ws FROM name_trim)),
        btrim(coalesce(m.value->>'lastName',''),(SELECT ws FROM name_trim))) t(first,last)
    CROSS JOIN LATERAL (SELECT CASE WHEN t.first='' AND t.last='' THEN ''
        ELSE jsonb_build_array(t.first,t.last)::text END) p(part)
    GROUP BY b.source_key
), rows AS MATERIALIZED (
    SELECT b.*,CASE WHEN NOT active THEN 'inactive'
        WHEN email_deliverable THEN 'deliverable'
        WHEN head_count=0 THEN 'no_head' WHEN address_count=0 THEN 'no_address'
        WHEN NOT email_eligible THEN 'invalid_address'
        ELSE 'provider_refused' END AS reason,
        -- The Family as shown (family_names.family_heads_name): "Squyres,
        -- Tracy and Jeff", or "Kelly / Thieneman, Maureen / Linda" when the
        -- heads have other surnames (#2320). Search matches it and
        -- same-surname Families sort by it; the page computes it again for
        -- display.
        stewardship_family_heads_name_v1(family_name,n.parts) AS display_name
    FROM base b LEFT JOIN head_names n ON n.source_key=b.source_key
), member_matches AS MATERIALIZED (
    -- Families with an active Member whose name contains the search (#664):
    -- first and last name, or nickname and last name. One set-based pass
    -- over the snapshot's Members, never a per-Family subquery, and none at
    -- all for an empty search. The matched Member is never returned.
    SELECT DISTINCT m.family_key FROM members m CROSS JOIN options o
    WHERE o.f->>'search'<>''
      AND (position(lower(o.f->>'search') IN lower(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')))>0
          OR position(lower(o.f->>'search') IN lower(concat_ws(' ',m.value->>'nickName',m.value->>'lastName')))>0)
), filtered AS MATERIALIZED (
    -- 'postal' selects the mail-merge columns only; it does not narrow the
    -- rows, so mailing details cover exactly the filtered Families (#202).
    SELECT r.* FROM rows r CROSS JOIN options o
    WHERE (NOT (parameters->>'exact')::boolean OR family_id=(parameters->>'family_id')::uuid)
      AND (o.f->>'reason'='any' OR reason=o.f->>'reason')
      AND (o.f->>'phone'='any' OR (
          EXISTS(SELECT 1 FROM contacts c WHERE c.source_key='family:'||r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          OR EXISTS(SELECT 1 FROM members m JOIN contacts c
              ON c.source_key='member:'||m.source_key WHERE m.family_key=r.source_key
              AND c.value->'phones'<>'{}'::jsonb)
          )=(o.f->>'phone'='yes'))
      -- The Response choices, as the response lists chose their rows.
      AND CASE o.f->>'response'
          WHEN 'submitted' THEN submitted_at IS NOT NULL
          WHEN 'more-than-once' THEN submissions>1
          WHEN 'started' THEN form_opened_at IS NOT NULL AND submitted_at IS NULL
          WHEN 'progressed' THEN progressed_at IS NOT NULL AND submitted_at IS NULL
          WHEN 'opened-only' THEN form_opened_at IS NOT NULL AND progressed_at IS NULL
          WHEN 'never-opened' THEN invited_at IS NOT NULL AND form_opened_at IS NULL
          WHEN 'link-followed' THEN invited_at IS NOT NULL AND form_opened_at IS NULL
              AND link_at IS NOT NULL
          WHEN 'link-not-followed' THEN invited_at IS NOT NULL AND form_opened_at IS NULL
              AND link_at IS NULL
          -- A submission implies the form was opened (form_opened_at).
          -- A response status, not a postal list (#951): only Families with a
          -- campaign record, since no invitation can go to one without.
          WHEN 'not-invited' THEN active AND family_id IS NOT NULL
              AND invited_at IS NULL AND form_opened_at IS NULL
          WHEN 'not-submitted' THEN active AND submitted_at IS NULL
          -- v1's choices, as the selection before v2 read them.
          WHEN 'yes' THEN active AND submitted_at IS NOT NULL
          WHEN 'no' THEN active AND submitted_at IS NULL
          ELSE true END
      AND CASE coalesce(o.f->>'check','any')
          WHEN 'anything' THEN mailing_name_blank OR envelope_zero
          WHEN 'mailing-name' THEN mailing_name_blank
          WHEN 'envelope' THEN envelope_zero
          ELSE true END
      AND CASE coalesce(o.f->>'reach','any')
          WHEN 'email' THEN email_deliverable
          WHEN 'mail' THEN NOT email_deliverable AND mailable
          WHEN 'neither' THEN NOT email_deliverable AND NOT mailable
          ELSE true END
      AND (o.f->>'search'='' OR position(lower(o.f->>'search') IN lower(display_name))>0
          OR position(lower(o.f->>'search') IN lower(search_name))>0
          OR position(o.f->>'search' IN family_duid::text)>0
          OR position(o.f->>'search' IN envelope_number)>0
          OR r.source_key IN (SELECT family_key FROM member_matches)
          OR EXISTS(SELECT 1 FROM addresses a
              CROSS JOIN LATERAL jsonb_each_text(a.fields) v
              WHERE a.source_key='family:'||r.source_key||':primary'
              AND position(lower(o.f->>'search') IN lower(v.value))>0))
), ordered AS (
    -- Surname first, then the whole shown name, then the DUID. The DUID and
    -- each response column sort either way, a response column with missing
    -- values last, then by name.
    SELECT r.*,row_number() OVER (ORDER BY
        CASE WHEN o.f->>'sort'='name' THEN lower(family_name) END,
        CASE WHEN o.f->>'sort'='name' THEN lower(display_name) END,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(family_name) END DESC NULLS LAST,
        CASE WHEN o.f->>'sort'='name_desc' THEN lower(display_name) END DESC NULLS LAST,
        CASE WHEN o.f->>'sort'='duid_desc' THEN family_duid END DESC,
        CASE o.f->>'sort' WHEN 'invited' THEN invited_at WHEN 'link' THEN link_at
            WHEN 'opened' THEN form_opened_at WHEN 'progressed' THEN progressed_at
            WHEN 'submitted' THEN submitted_at WHEN 'last' THEN last_submitted_at END,
        CASE o.f->>'sort' WHEN 'invited_desc' THEN invited_at WHEN 'link_desc' THEN link_at
            WHEN 'opened_desc' THEN form_opened_at WHEN 'progressed_desc' THEN progressed_at
            WHEN 'submitted_desc' THEN submitted_at WHEN 'last_desc' THEN last_submitted_at
            END DESC NULLS LAST,
        CASE WHEN o.f->>'sort'='submissions' THEN submissions END,
        CASE WHEN o.f->>'sort'='submissions_desc' THEN submissions END DESC NULLS LAST,
        CASE WHEN o.f->>'sort' NOT IN ('name','name_desc','duid','duid_desc') THEN lower(family_name) END,
        CASE WHEN o.f->>'sort' NOT IN ('name','name_desc','duid','duid_desc') THEN lower(display_name) END,
        family_duid) AS ordinal
    FROM filtered r CROSS JOIN options o
), page AS MATERIALIZED (
    SELECT * FROM ordered ORDER BY ordinal LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE 50 END OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*50 END
), page_heads AS MATERIALIZED (
    -- The shown page's heads in one set-based join; a per-row subquery
    -- rescanned every Member for each of the page's rows.
    SELECT p.source_key,jsonb_agg(jsonb_build_object('duid',h.head,
            'name',concat_ws(' ',nullif(n.first,''),nullif(n.last,'')),
            'first',n.first,'last',n.last)
            ORDER BY h.head::bigint) AS heads
    FROM page p
    CROSS JOIN LATERAL jsonb_array_elements_text(p.head_duids) h(head)
    JOIN members m ON m.source_key=h.head
    CROSS JOIN LATERAL (SELECT btrim(coalesce(m.value->>'firstName',''),(SELECT ws FROM name_trim)),
        btrim(coalesce(m.value->>'lastName',''),(SELECT ws FROM name_trim))) n(first,last)
    GROUP BY p.source_key
), details AS (
    -- Only the selected page constructs display-only private contact JSON.
    SELECT p.*,f.value->>'envelopeNumber' AS envelope,
        coalesce(ph.heads,'[]'::jsonb) AS heads,
        coalesce((SELECT jsonb_agg(jsonb_build_object('owner',v.owner,'kind',phone.key,
            'value',phone.value) ORDER BY v.owner,phone.key,phone.value)
            FROM (
                SELECT 'Family' AS owner,c.value FROM contacts c
                WHERE c.source_key='family:'||p.source_key
                UNION ALL
                SELECT btrim(concat_ws(' ',m.value->>'firstName',m.value->>'lastName')),
                    c.value FROM members m
                    JOIN contacts c ON c.source_key='member:'||m.source_key
                WHERE m.family_key=p.source_key
            ) v CROSS JOIN LATERAL jsonb_each_text(v.value->'phones') phone),
            '[]'::jsonb) AS phones,
        coalesce((SELECT a.fields FROM addresses a
            WHERE a.source_key='family:'||p.source_key||':primary'),
            '{}'::jsonb) AS address
    FROM page p JOIN listed_source f ON f.source_key=p.source_key
    LEFT JOIN page_heads ph ON ph.source_key=p.source_key
)
SELECT jsonb_build_object('metadata',to_jsonb(s)||jsonb_build_object('report_mode',report_mode,
        'counted_at',CASE WHEN funnel_needed THEN counted END),
    'total',(SELECT count(*) FROM filtered),
    'active_total',(SELECT count(*) FROM rows WHERE active),
    'postal_total',(SELECT count(*) FROM rows WHERE active AND NOT email_deliverable),
    'unreachable_total',(SELECT count(*) FROM rows
        WHERE active AND NOT email_deliverable AND NOT mailable),
    'rows',coalesce((SELECT jsonb_agg(
        to_jsonb(p)-ARRAY['ordinal','head_count','address_count','search_name','envelope_number','source_key','display_name','head_duids']
        ORDER BY ordinal) FROM details p),'[]'::jsonb)) INTO answer FROM source s;
    -- END DIRECTORY SELECTION
    RETURN answer;
END $$;

DO $check$
BEGIN
    -- The name functions are installed once, as declared: invokers with the
    -- pinned search_path, immutable, parallel safe, returning text, with the
    -- default grants, and the two replaced ones carry the new bodies.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND NOT p.prosecdef AND p.proacl IS NULL
          AND p.proparallel='s' AND p.provolatile='i'
          AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
          AND p.prorettype='text'::regtype
          AND ((p.proname='stewardship_name_series_v1'
                AND pg_get_function_identity_arguments(p.oid)='parts text[]')
            OR (p.proname='stewardship_family_head_part_v1'
                AND pg_get_function_identity_arguments(p.oid)='head jsonb, surname text'
                AND p.prosrc LIKE '%jsonb_build_array(n.first,n.last)::text%')
            OR (p.proname='stewardship_family_heads_name_v1'
                AND pg_get_function_identity_arguments(p.oid)='surname text, parts text[]'
                AND p.prosrc LIKE '%casefold(surname COLLATE pg_catalog.pg_unicode_fast)%')))<>3
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_name_series_v1',
               'stewardship_family_head_part_v1','stewardship_family_heads_name_v1'))<>3
    THEN
        RAISE EXCEPTION 'Migration 0194 (mixed-surname Family names) is not installed as declared';
    END IF;
    -- They name the issue's Families as family_names.family_heads_name does:
    -- two surnames, a surname group of two, every head sharing the surname,
    -- none sharing it, letter case, a head without a last name, an inactive
    -- or missing head, and no heads.
    IF stewardship_name_series_v1(ARRAY['A','',NULL,'B','C']) IS DISTINCT FROM 'A, B and C'
       OR stewardship_name_series_v1(ARRAY['A','B']) IS DISTINCT FROM 'A and B'
       OR stewardship_name_series_v1(NULL) IS DISTINCT FROM ''
       OR stewardship_family_heads_name_v1('Kelly',ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Linda","lastName":"Thieneman","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Maureen","lastName":"Kelly","active":true}','Kelly')])
           IS DISTINCT FROM 'Kelly / Thieneman, Maureen / Linda'
       OR stewardship_family_heads_name_v1('Kelly',ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Maureen","lastName":"Kelly","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Linda","lastName":"Thieneman","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Bob","lastName":"KELLY","active":true}','Kelly')])
           IS DISTINCT FROM 'Kelly / Thieneman, Maureen and Bob / Linda'
       OR stewardship_family_heads_name_v1('Squyres',ARRAY[
           stewardship_family_head_part_v1('{"firstName":" Tracy","lastName":"Squyres","active":true}','Squyres'),
           stewardship_family_head_part_v1('{"firstName":"Jeff","lastName":"squyres","active":true}','Squyres'),
           stewardship_family_head_part_v1('{"firstName":"Cy","lastName":"Squyres","active":false}','Squyres'),
           stewardship_family_head_part_v1(NULL,'Squyres')])
           IS DISTINCT FROM 'Squyres, Tracy and Jeff'
       OR stewardship_family_heads_name_v1('Kelly',ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Bo","lastName":"Fox","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Cy","lastName":"fox","active":true}','Kelly')])
           IS DISTINCT FROM 'Lee / Fox, Ann / Bo and Cy'
       OR stewardship_family_heads_name_v1('Kelly',ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Pat","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Maureen","lastName":"Kelly","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":" ","lastName":"Ray","active":true}','Kelly'),
           stewardship_family_head_part_v1('{"firstName":"Linda","lastName":"Thieneman","active":true}','Kelly')])
           IS DISTINCT FROM 'Kelly / Ray / Thieneman, Maureen / Linda / Pat'
       OR stewardship_family_heads_name_v1('Lee',ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Di","active":true}','Lee'),
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','Lee')])
           IS DISTINCT FROM 'Lee, Di and Ann'
       OR stewardship_family_heads_name_v1('Lee',ARRAY['',NULL]) IS DISTINCT FROM 'Lee'
       OR stewardship_family_heads_name_v1('Lee',NULL) IS DISTINCT FROM 'Lee'
    THEN
        RAISE EXCEPTION 'Migration 0194 Family name functions do not name Families as Python does';
    END IF;
    -- Both directory selections, once, invoker, stable, build the shown name
    -- with the shared function and no longer inline.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_directory_report_v1','stewardship_directory_report_v2')
          AND pg_get_function_identity_arguments(p.oid)=
              'campaign_uuid uuid, parameters jsonb, page_number integer'
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
          AND p.prosrc LIKE '%stewardship_family_heads_name_v1(family_name,n.parts) AS display_name%'
          AND p.prosrc LIKE '%ELSE jsonb_build_array(t.first,t.last)::text END) p(part)%'
          AND p.prosrc NOT LIKE '%t.last=b.family_name%')<>2
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_directory_report_v1',
               'stewardship_directory_report_v2'))<>2 THEN
        RAISE EXCEPTION 'Migration 0194 (mixed-surname Family names) did not replace the directory selections';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_family_head_part_v1',
            'stewardship_family_heads_name_v1','stewardship_directory_report_v1',
            'stewardship_directory_report_v2'))
        IS DISTINCT FROM current_setting('stewardship.migration_0194_acl', true) THEN
        RAISE EXCEPTION 'Migration 0194 changed the replaced functions'' grants or settings';
    END IF;
END
$check$;
