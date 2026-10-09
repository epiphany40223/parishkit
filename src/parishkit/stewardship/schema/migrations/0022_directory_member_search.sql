-- Frozen forward migration file 0022 (the repository-wide file sequence): the
-- Family directory search also finds a Family by any active Member's name and
-- by its envelope number (#664). This file is installed by its Django
-- migration and must never change once released;
-- tests/stewardship/test_schema_migration_files.py
-- pins its digest and checks that its copy of the replaced function still
-- equals the fresh-install baseline's (schema/directory_reports.sql). A fresh
-- install runs the baseline, 0002 to 0021 and then this; the baseline already
-- carries the replaced body, so the install ends in the same catalog as an
-- upgraded database.
--
-- stewardship_directory_report_v1 is the one selection behind the Family
-- directory page, its exports and the header's Find a Family box (#561), so
-- all three gain the new matches together. Its search now also matches:
-- an active Member's first and last name (or nickname and last name), from
-- one set-based pass over the snapshot's Members (member_matches), and the
-- envelope number as a substring, like the DUID. The output is unchanged:
-- the new envelope_number column is stripped like search_name, and a matched
-- Member is never returned, so results show only what the directory already
-- shows. The signature, STABLE, jit=off and the trusted search_path are
-- kept; the function is not SECURITY DEFINER, before or after. CREATE OR
-- REPLACE keeps the owner and grants; the DO block below checks the ACL is
-- the one recorded before the replacement.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The function's grants before the replacement (with how many definitions
-- there are), compared in the DO block. A transaction-local setting holds
-- them, not a temporary table: the migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0022_acl', (
    SELECT count(*)||':'||coalesce(string_agg(coalesce(p.proacl::text,'default'),';'),'')
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v1'), true);

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
       OR NOT parameters ?& ARRAY['filters','postal','exact','family_id']
       OR parameters-ARRAY['filters','postal','exact','family_id']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'postal') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'exact') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(parameters->'family_id') NOT IN ('null','string')
       OR (parameters->>'family_id' IS NOT NULL AND
           parameters->>'family_id' !~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
       OR (parameters->'exact'='false'::jsonb AND parameters->>'family_id' IS NOT NULL)
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f ?& ARRAY['search','reason','phone','response','sort']
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
            'Family') AS family_name,
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
    -- The Family as shown (family_names.family_heads_name): the surname,
    -- then the heads' first names ("A", "A and B", "A, B and C"), a head of
    -- another surname in full. One set-based join over every head: a
    -- per-Family subquery rescanned every Member for every Family.
    SELECT b.source_key,array_agg(p.part ORDER BY h.head::bigint)
        FILTER (WHERE p.part<>'') AS parts
    FROM base b
    CROSS JOIN LATERAL jsonb_array_elements_text(b.head_duids) h(head)
    JOIN members m ON m.source_key=h.head
    CROSS JOIN LATERAL (SELECT btrim(coalesce(m.value->>'firstName',''),(SELECT ws FROM name_trim)),
        btrim(coalesce(m.value->>'lastName',''),(SELECT ws FROM name_trim))) t(first,last)
    CROSS JOIN LATERAL (SELECT CASE WHEN t.last=b.family_name THEN t.first
        ELSE concat_ws(' ',nullif(t.first,''),nullif(t.last,'')) END) p(part)
    GROUP BY b.source_key
), rows AS MATERIALIZED (
    SELECT b.*,CASE WHEN email_deliverable THEN 'deliverable'
        WHEN head_count=0 THEN 'no_head' WHEN address_count=0 THEN 'no_address'
        WHEN NOT email_eligible THEN 'invalid_address'
        ELSE 'provider_refused' END AS reason,
        -- The Family as shown (family_names.family_heads_name): the surname,
        -- then the heads' first names ("A", "A and B", "A, B and C"), a head
        -- of another surname in full. Search matches it and same-surname
        -- Families sort by it; the page computes it again for display.
        family_name||coalesce(', '||(SELECT CASE WHEN cardinality(n.parts)<3
                THEN array_to_string(n.parts,' and ')
                ELSE array_to_string(n.parts[1:cardinality(n.parts)-1],', ')
                    ||' and '||n.parts[cardinality(n.parts)] END),'') AS display_name
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

DO $check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v1'
          AND pg_get_function_identity_arguments(p.oid)
              ='campaign_uuid uuid, parameters jsonb, page_number integer'
          AND p.prorettype='jsonb'::regtype AND p.provolatile='s'
          AND NOT p.prosecdef
          AND p.proconfig @> ARRAY['jit=off','search_path=pg_catalog, public, pg_temp']
          AND p.prosrc LIKE '%member_matches AS MATERIALIZED%'
          AND p.prosrc LIKE '%position(o.f->>''search'' IN envelope_number)>0%'
          AND p.prosrc LIKE '%''search_name'',''envelope_number'',%') THEN
        RAISE EXCEPTION 'stewardship_directory_report_v1 was not replaced with the #664 search';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v1')<>1 THEN
        RAISE EXCEPTION 'stewardship_directory_report_v1 must have exactly one definition';
    END IF;
    IF current_setting('stewardship.migration_0022_acl', true) IS DISTINCT FROM (
           SELECT '1:'||coalesce(p.proacl::text,'default')
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v1') THEN
        RAISE EXCEPTION 'stewardship_directory_report_v1 lost or changed its grants';
    END IF;
END
$check$;
