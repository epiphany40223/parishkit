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
-- ignoring letter case (casefold; the Family surname shown as recorded, each
-- other surname as its first head spells it); heads without a last name
-- follow in full; a surname whose heads have no first names
-- leaves no empty part. When no head shares the Family surname only the
-- heads' own surnames lead ("Lee / Fox, Ann / Bo"). When every head shares
-- it, the name is unchanged. A Family with a blank lastName (named by a
-- stand-in: its mailing name, first name or "Name not recorded in
-- ParishSoft") is named the same way from its heads' own surnames ("Lee,
-- Ann and Bo"); only when no head has a surname does the stand-in lead, as
-- before. The Financial stewardship and Additional information searches
-- also match the Family surname (or its stand-in), which the shown name may
-- no longer hold; the shown name holds every head's surname. Both rules
-- are the Administrator's (2026-10-31).
--
-- Created, only here:
--   stewardship_name_series_v1(text[])  family_names.name_series: "A",
--                                       "A and B", "A, B and C"; blank or
--                                       NULL parts skipped
--   stewardship_family_last_name_v1(jsonb)
--                                       family_names.family_last_name: the
--                                       stripped lastName, '' when blank
--   stewardship_family_heads_name_v2(text,boolean,text[])
--                                       builds the name above from the
--                                       heads' parts, told whether the
--                                       surname is the recorded lastName
-- Replaced functions, each with its signature, attributes and grants kept:
--   stewardship_family_head_part_v1   (0045) one head's part is now its
--                                     first and last name as a JSON array
--                                     text, '["Ann","Lee"]', so the whole
--                                     name can group heads by surname; ''
--                                     for an inactive or nameless head, as
--                                     before. Its surname argument is no
--                                     longer read; it stays so no caller
--                                     changes.
--   stewardship_family_heads_name_v1  (0045) now calls
--                                     stewardship_family_heads_name_v2 with
--                                     the surname recorded; no selection
--                                     calls it any more. Its signature is
--                                     kept but its input contract changes:
--                                     parts must be the JSON array text
--                                     stewardship_family_head_part_v1 now
--                                     returns; 0045's plain text parts
--                                     ('Ann Lee') raise invalid input
--                                     syntax for type json
--   stewardship_family_display_name_v1
--                                     (0045) calls
--                                     stewardship_family_heads_name_v2 with
--                                     the Family row's lastName
--   stewardship_directory_report_v1   (the baseline; 0094 last replaced it)
--   stewardship_directory_report_v2   (migration-owned; 0094) each built the
--                                     name inline; now each encodes its
--                                     heads' parts the same way and calls
--                                     stewardship_family_heads_name_v2, so
--                                     search and sort match the page; each
--                                     row also returns surname_recorded,
--                                     which the page names the Family by
--   stewardship_financial_report_v1   (the baseline; 0182 last replaced it)
--   stewardship_information_report_v1 (the baseline; 0175 last replaced it)
--   stewardship_information_report_v2 (migration-owned; 0175) each calls
--                                     stewardship_family_heads_name_v2 with
--                                     the surname recorded, and its search
--                                     also matches the Family surname; the
--                                     returned rows are unchanged
-- The selections are copied whole from their newest definitions with only
-- those changes, each ?, ?& and @> written OPERATOR(pg_catalog.x) (#2166,
-- operations spec, post-launch schema policy). None of these functions is
-- SECURITY DEFINER, before or after, and none has a REVOKE, so there is no
-- ALTER FUNCTION or REVOKE to repeat. The DO block below checks the
-- attributes, settings, grants recorded before the replacement and the new
-- bodies, names sample Families as Python does, and refuses each older
-- body. Reversing needs its own forward migration.
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
        'stewardship_family_heads_name_v1','stewardship_family_display_name_v1',
        'stewardship_directory_report_v1','stewardship_directory_report_v2',
        'stewardship_financial_report_v1','stewardship_information_report_v1',
        'stewardship_information_report_v2')), true);

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

-- family_names.family_last_name: the Family's stripped lastName, '' when it
-- is blank, missing or not a string (as Python's _text reads it), or when
-- family is NULL. family_display_name (stewardship_family_surname_v1) puts a
-- stand-in in place of a blank one; this says when it did.
CREATE FUNCTION public.stewardship_family_last_name_v1(family jsonb)
    RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT coalesce(stewardship_name_strip_v1(CASE WHEN jsonb_typeof(family->'lastName')='string'
        THEN family->>'lastName' END),'')
$$;

-- family_names.family_heads_name from the heads' parts in head DUID order
-- (stewardship_family_head_part_v1's JSON arrays; '' or NULL parts are
-- skipped). recorded says whether surname is the Family's own lastName
-- (stewardship_family_last_name_v1 is not ''); a stand-in for a blank one
-- matches no head. Heads with a last name group under it, compared by
-- casefold (Python's str.casefold), each group placed at its first head and
-- shown as its first head spells it, except the recorded Family surname's
-- group, which shows surname as given. When no group but the recorded Family
-- surname's is left, the name is unchanged: the surname, then every head's
-- first name in head order ("Squyres, Tracy and Jeff"; a head without a
-- last name has only a first name here). Otherwise the surnames lead, " / "
-- between them (the Family surname first when a head has it, then the
-- other groups), then a comma and each group's first names (name_series)
-- in the same order, then the first names of heads without a last name,
-- " / " between the non-blank parts: "Kelly / Thieneman, Maureen and Bob /
-- Linda"; without a recorded surname "Lee, Ann and Bo". No parts at all
-- gives the surname alone.
CREATE FUNCTION public.stewardship_family_heads_name_v2(surname text, recorded boolean, parts text[])
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
            coalesce(recorded AND h.key=casefold(surname COLLATE pg_catalog.pg_unicode_fast),
                false) AS own
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

-- The two-argument form names a Family whose surname is its recorded
-- lastName. No selection calls it any more (each passes recorded to
-- stewardship_family_heads_name_v2); it stays, signature unchanged, so it
-- cannot name a Family differently from the shared rule. Its input contract
-- did change: every non-empty part must be stewardship_family_head_part_v1's
-- JSON array text ('["Ann","Lee"]'). A 0045-style plain text part ('Ann
-- Lee') now raises invalid input syntax for type json; no caller passes one
-- (every selection that did is replaced in this file).
CREATE OR REPLACE FUNCTION public.stewardship_family_heads_name_v1(surname text, parts text[])
    RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT stewardship_family_heads_name_v2(surname,true,parts)
$$;

-- snapshot_names.snapshot_family_names for one Family (0045's body), now
-- with recorded from the Family row, so a blank lastName names the Family
-- by its heads' own surnames.
CREATE OR REPLACE FUNCTION public.stewardship_family_display_name_v1(
    snapshot_id uuid, duid bigint, fallback text
) RETURNS text LANGUAGE sql STABLE PARALLEL SAFE
    SET search_path TO pg_catalog, public, pg_temp
AS $$
    SELECT stewardship_family_heads_name_v2(f.surname,
        stewardship_family_last_name_v1(f.family)<>'',ARRAY(
        SELECT stewardship_family_head_part_v1(mp.canonical::jsonb,f.surname)
        FROM jsonb_array_elements_text(CASE
            WHEN jsonb_typeof(f.family->'active_head_duids')='array'
            THEN f.family->'active_head_duids' END) h(head)
        JOIN stewardship_snapshot_member sm
            ON sm.snapshot_id=$1 AND sm.source_key=h.head
        JOIN stewardship_source_member mp ON mp.id=sm.payload_id
        ORDER BY h.head::bigint))
    FROM (SELECT d.family,stewardship_family_surname_v1(d.family,$3) AS surname
        FROM stewardship_snapshot_family sf
        JOIN stewardship_source_family fp ON fp.id=sf.payload_id
        CROSS JOIN LATERAL (SELECT fp.canonical::jsonb) d(family)
        WHERE sf.snapshot_id=$1 AND sf.source_key=$2::text) f
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
        -- Whether family_name is the Family's own surname, not a stand-in
        -- for a blank lastName (family_names.family_last_name, #2320).
        nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),'') IS NOT NULL
            AS surname_recorded,
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
        -- heads have other surnames, or when a blank lastName leaves the
        -- Family no surname of its own (#2320). Search matches it and
        -- same-surname Families sort by it; the page computes it again for
        -- display, from the rows' surname_recorded.
        stewardship_family_heads_name_v2(family_name,surname_recorded,n.parts) AS display_name
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
        -- Whether family_name is the Family's own surname, not a stand-in
        -- for a blank lastName (family_names.family_last_name, #2320).
        nullif(btrim(f.value->>'lastName',(SELECT ws FROM name_trim)),'') IS NOT NULL
            AS surname_recorded,
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
        -- heads have other surnames, or when a blank lastName leaves the
        -- Family no surname of its own (#2320). Search matches it and
        -- same-surname Families sort by it; the page computes it again for
        -- display, from the rows' surname_recorded.
        stewardship_family_heads_name_v2(family_name,surname_recorded,n.parts) AS display_name
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

-- stewardship_financial_report_v1: copied from its newest definition (the
-- baseline, financial_reports.sql; 0182 last replaced it); only the shown
-- name and the search changed (#2320).
CREATE OR REPLACE FUNCTION public.stewardship_financial_report_v1(
    -- A NULL page number returns the complete, unpaged result. The caller owns
    -- the page size and must state it, so its paging arithmetic cannot drift
    -- from the rows returned here; it is ignored for a complete result.
    campaign_uuid uuid, parameters jsonb, page_number integer, page_size integer
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; proof jsonb:=parameters->'proof';
    answer jsonb; bad boolean;
    -- Whether the dates are days in the viewer's browser zone (#558), and
    -- each date's bound there: From is the instant its day begins, Through
    -- the instant the next day begins (exclusive).
    zoned boolean:=false; first_from timestamptz; first_before timestamptz;
    latest_from timestamptz; latest_before timestamptz;
    money_text constant text:='^(0|[1-9][0-9]{0,8})(\.[0-9]{2})?$';
    -- Canonical lowercase text, so identities compare without a fallible cast.
    uuid_text constant text:='^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
BEGIN
    -- Each step relies only on what an earlier statement established. PL/pgSQL
    -- runs statements in order, which SQL does not promise for the terms of one
    -- condition: a container operator or a cast placed beside its own guard could
    -- run first and fail with its own error, echoing a filter value, instead of
    -- this closed refusal. An undecidable step refuses.
    bad:=jsonb_typeof(parameters) IS DISTINCT FROM 'object';
    IF NOT bad THEN
        bad:=coalesce(NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','proof']
            OR parameters-ARRAY['filters','proof']<>'{}'::jsonb
            OR jsonb_typeof(f) IS DISTINCT FROM 'object'
            OR jsonb_typeof(proof) NOT IN ('null','object')
            OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
            OR (page_number IS NOT NULL
                AND (page_size IS NULL OR page_size NOT BETWEEN 1 AND 200)),true);
    END IF;
    IF NOT bad AND jsonb_typeof(proof)='object' THEN
        bad:=coalesce(NOT proof OPERATOR(pg_catalog.?&) ARRAY['snapshot','configuration']
            OR proof-ARRAY['snapshot','configuration']<>'{}'::jsonb
            OR jsonb_typeof(proof->'snapshot') IS DISTINCT FROM 'string'
            OR jsonb_typeof(proof->'configuration') IS DISTINCT FROM 'string'
            OR NOT proof->>'snapshot' ~ uuid_text
            OR NOT proof->>'configuration' ~ uuid_text,true);
    END IF;
    IF NOT bad THEN
        bad:=coalesce(NOT f OPERATOR(pg_catalog.?&) ARRAY['search','active','first_start','first_end',
                'latest_start','latest_end','pledge_min','pledge_max','amount',
                'frequency','share','sort']
            OR f-ARRAY['search','active','first_start','first_end','latest_start',
                'latest_end','pledge_min','pledge_max','amount','frequency','share',
                'sort','zone']<>'{}'::jsonb
            OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
            OR length(f->>'search')>200
            OR (f->>'share' NOT IN ('any','none') AND NOT f->>'share' ~ uuid_text)
            OR f->>'active' NOT IN ('any','active','inactive','unavailable')
            OR f->>'amount' NOT IN ('any','zero','nonzero','cannot_give')
            OR f->>'frequency'
                NOT IN ('any','none','weekly','monthly','quarterly','annual')
            OR f->>'sort'
                NOT IN ('name','name_desc','newest','oldest','pledge','pledge_desc',
                    'duid','duid_desc')
            OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
                WHERE d.key IN ('first_start','first_end','latest_start','latest_end')
                  AND d.value<>'' AND (NOT d.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'
                      OR NOT pg_input_is_valid(d.value,'date')))
            OR EXISTS(SELECT 1 FROM jsonb_each_text(f) d
                WHERE d.key IN ('pledge_min','pledge_max') AND d.value<>''
                  AND NOT d.value ~ money_text),true);
    END IF;
    IF NOT bad THEN
        -- Ranges, once every bound is known to be well formed. An absent bound
        -- becomes NULL, so no term here depends on another to be safe.
        bad:=coalesce(nullif(f->>'first_start','')>nullif(f->>'first_end',''),false)
            OR coalesce(nullif(f->>'latest_start','')>nullif(f->>'latest_end',''),false)
            OR coalesce(nullif(f->>'pledge_min','')::numeric
                >nullif(f->>'pledge_max','')::numeric,false);
    END IF;
    IF NOT bad AND f OPERATOR(pg_catalog.?) 'zone' THEN
        -- Days are the viewer's browser zone (#558): one is required with a
        -- date and is always one the server's time zone catalog knows.
        -- Filters without the key, from a release before migration 0110,
        -- keep the campaign-zone days.
        bad:=coalesce((f->>'first_start'<>'' OR f->>'first_end'<>''
                OR f->>'latest_start'<>'' OR f->>'latest_end'<>'')
                AND f->>'zone'='',true)
            OR coalesce(f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names
                WHERE name=stewardship_timezone_name_v1(f->>'zone')),true);
        zoned:=true;
    END IF;
    IF bad THEN
        RAISE EXCEPTION 'Invalid financial report parameters' USING ERRCODE='23514';
    END IF;
    IF zoned THEN
        -- Each bound once per call, never per row; a daylight-saving day is
        -- 23 or 25 hours long.
        first_from:=CASE WHEN f->>'first_start'<>'' THEN
            stewardship_browser_day_start_v1((f->>'first_start')::date,f->>'zone') END;
        first_before:=CASE WHEN f->>'first_end'<>'' THEN
            stewardship_browser_day_start_v1((f->>'first_end')::date+1,f->>'zone') END;
        latest_from:=CASE WHEN f->>'latest_start'<>'' THEN
            stewardship_browser_day_start_v1((f->>'latest_start')::date,f->>'zone') END;
        latest_before:=CASE WHEN f->>'latest_end'<>'' THEN
            stewardship_browser_day_start_v1((f->>'latest_end')::date+1,f->>'zone') END;
    END IF;

WITH selected AS MATERIALIZED (
    -- The campaign configuration row the proof names. It is not a submission's
    -- applied configuration version, which the response rows carry below.
    SELECT c.id,cc.id AS active_configuration_id,cc.name,cc.timezone,cc.values,
        CASE WHEN c.state='archived' THEN k.source_snapshot_id
             ELSE sc.snapshot_id END AS source_id
    FROM stewardship_campaign c
    JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
    LEFT JOIN stewardship_campaign_credentials k ON k.campaign_id=c.id
    LEFT JOIN stewardship_source_current sc ON sc.singleton
    WHERE c.id=campaign_uuid
), source AS MATERIALIZED (
    SELECT x.*,s.generation AS source_generation,s.promoted_at AS source_as_of,
        statement_timestamp() AS observed_at,
        -- Source money is shown only with a completeness proof of exactly the
        -- snapshot and configuration selected above; any other proof withholds.
        CASE WHEN v.proven
            AND pg_input_is_valid(s.cursor->'load'->>'giving_as_of_date','date')
            THEN (s.cursor->'load'->>'giving_as_of_date')::date END AS giving_through,
        CASE WHEN v.proven THEN s.cursor->>'full_started_at' END AS giving_observed_at
    FROM selected x JOIN stewardship_source_snapshot s ON s.id=x.source_id
    CROSS JOIN LATERAL (SELECT coalesce(
        proof->>'snapshot'=x.source_id::text
        AND proof->>'configuration'=x.active_configuration_id::text,false)
        AS proven) v
    WHERE s.state='promoted' AND s.compacted_at IS NULL
        AND x.values->'modules' OPERATOR(pg_catalog.?) 'financial'
), edits AS MATERIALIZED (
    -- Each Family's pledge Edit in force (#2196): its latest pledge review
    -- row, when the selection that places Pledges' stages (0181's
    -- stewardship_pledge_review_states_v1) holds it in force and its
    -- decision is Accepted (edited). Entered by hand and the undo of a
    -- terminal step carry that decision with its values, so they count
    -- too; Back to review of a decision, Accept, Ignore and a newer,
    -- different pledge answer from the Family leave none.
    SELECT k.family_id,v.reviewed_annual_pledge,v.reviewed_frequency,v.reviewed_shares
    FROM source x
    CROSS JOIN LATERAL stewardship_pledge_review_states_v1(x.id,NULL) k
    JOIN stewardship_pledge_review v ON v.id=k.review_id
    WHERE k.in_force AND v.decision='accepted_edited'
), responses AS MATERIALIZED (
    -- The pledge in force (#2196): an Edit's amount, frequency and share
    -- methods while it is in force, else the Family's own answer. An
    -- edited pledge reads as the reviewed Family form shows it (0181's
    -- selection, rule 1): above zero the reviewer's frequency, and the
    -- reviewer's share methods where the Edit changed them, else the
    -- Family's own; a zero pledge no frequency and no share methods, with
    -- the Family's own Cannot contribute answer. The Family's own parts are
    -- read as 0181's rule reads them (stewardship_pledge_answer_v1), so an
    -- answer without share methods carries none. Every filter, sort and
    -- summary below reads these.
    SELECT s.id,s.submitted_at,s.family_version,
        coalesce(e.reviewed_annual_pledge,s.annual_pledge) AS annual_pledge,
        s.configuration_id,
        i.family_duid,first.submitted_at AS first_submitted_at,
        CASE WHEN e.family_id IS NULL THEN s.answers->'financial'
            ELSE s.answers->'financial'||jsonb_build_object(
                'frequency',CASE WHEN e.reviewed_annual_pledge>0
                    THEN coalesce(e.reviewed_frequency,'') ELSE '' END,
                'cannot_give',CASE WHEN e.reviewed_annual_pledge>0 THEN 'false'::jsonb
                    ELSE stewardship_pledge_answer_v1(s.answers,s.annual_pledge)->'cannot_give'
                    END,
                'shares',CASE WHEN e.reviewed_annual_pledge>0
                    THEN coalesce(e.reviewed_shares,
                        stewardship_pledge_answer_v1(s.answers,s.annual_pledge)->'shares')
                    ELSE '{}'::jsonb END) END AS financial,
        -- The Family's own answer beside an Edit in force (NULL without
        -- one), from its current response: the page's Edited marker words
        -- it, and Pledges and the Family hub show it in the Family's
        -- answer's place, as they did before 0182. After a reviewed form
        -- sent the edited pledge back as shown, that answer is the edited
        -- pledge.
        CASE WHEN e.family_id IS NOT NULL THEN jsonb_build_object(
            'annual_pledge',s.annual_pledge::numeric(24,2)::text,
            'frequency',nullif(s.answers->'financial'->>'frequency',''),
            'cannot_give',coalesce(s.answers->'financial'->'cannot_give'='true'::jsonb,false),
            'shares',s.answers->'financial'->'shares') END AS family_answer,
        coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
            nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
            nullif(btrim(concat_ws(' ',
                nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
            -- No name (NULL) for a Family the data no longer has: the page
            -- shows its ParishSoft status badge alone, and it sorts after
            -- the names (#1216). A present Family without a usable name
            -- reads so.
            CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS family_name,
        -- The surname that leads the shown name, as Python names it
        -- (migration 0045's shared functions); family_name above keeps its
        -- own trim for the returned rows.
        stewardship_family_surname_v1(p.canonical::jsonb,
            CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
        -- Whether surname is the Family's own lastName, not a stand-in for
        -- a blank one (family_names.family_last_name): without one, the
        -- heads' own surnames lead the shown name (#2320).
        stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
        -- Whether the Family is still one of the campaign's Families (NULL
        -- when it is not in the data), for its status badge and the Active
        -- filter (#1393). A row loaded under a Families rule names the rule
        -- (campaign_families) and its portal_eligible is "matches the rule";
        -- a legacy row names none and keeps ParishSoft's active.
        CASE WHEN p.canonical::jsonb OPERATOR(pg_catalog.?) 'campaign_families'
                AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
            THEN (p.canonical::jsonb->'portal_eligible')::boolean
            WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
            THEN (p.canonical::jsonb->'active')::boolean END AS active,
        CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
            THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
        -- Whether Staff entered this effective response for the Family
        -- through Open form (#529, #794): a yes or no, never who.
        s.entered_by_id IS NOT NULL AS staff_entered
    FROM source x
    JOIN stewardship_family_campaign i ON i.campaign_id=x.id
    JOIN stewardship_submission s ON s.id=i.effective_submission_id
        AND s.mode='live' AND s.campaign_id=x.id AND s.answers OPERATOR(pg_catalog.?) 'financial'
    JOIN stewardship_submission first ON first.id=i.first_live_submission_id
    LEFT JOIN stewardship_snapshot_family m
        ON m.snapshot_id=x.source_id AND m.source_key=i.family_duid::text
    LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
    LEFT JOIN edits e ON e.family_id=i.id
), head_names AS MATERIALIZED (
    -- Each Family's active heads as the shown name lists them, in DUID order
    -- (stewardship_family_head_part_v1), so search and sort match what the
    -- page shows (#960). One set-based join over every head, as in the
    -- directory selection, skipped when neither a search nor a name sort
    -- reads the name.
    SELECT r.family_duid,array_agg(
            stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
            ORDER BY h.head::bigint) AS parts
    FROM responses r CROSS JOIN source x
    CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
    JOIN stewardship_snapshot_member mm
        ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
    JOIN stewardship_source_member sm ON sm.id=mm.payload_id
    WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
    GROUP BY r.family_duid
), named AS MATERIALIZED (
    -- "Surname, A and B" (family_names.family_heads_name).
    SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
        AS display_name
    FROM responses r LEFT JOIN head_names n ON n.family_duid=r.family_duid
), filtered AS MATERIALIZED (
    SELECT r.*,nullif(r.financial->>'frequency','') AS frequency
    FROM named r CROSS JOIN source x
    -- Search matches the shown name, which names every head's surname,
    -- and the Family surname (or its stand-in), which it may not (#2320).
    WHERE (f->>'search'='' OR position(lower(f->>'search') IN lower(r.display_name))>0
            OR position(lower(f->>'search') IN lower(r.surname))>0
            OR position(f->>'search' IN r.family_duid::text)>0)
        AND (f->>'active'='any' OR (f->>'active'='active' AND r.active IS TRUE)
            OR (f->>'active'='inactive' AND r.active IS FALSE)
            OR (f->>'active'='unavailable' AND r.active IS NULL))
        -- Zoned (#558): from local midnight of the From day to local midnight
        -- after the Through day, in the viewer's zone. Otherwise the
        -- campaign-zone days, unchanged.
        AND (f->>'first_start'='' OR CASE WHEN zoned THEN r.first_submitted_at>=first_from
            ELSE (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'first_start','')::date END)
        AND (f->>'first_end'='' OR CASE WHEN zoned THEN r.first_submitted_at<first_before
            ELSE (r.first_submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'first_end','')::date END)
        AND (f->>'latest_start'='' OR CASE WHEN zoned THEN r.submitted_at>=latest_from
            ELSE (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            >=nullif(f->>'latest_start','')::date END)
        AND (f->>'latest_end'='' OR CASE WHEN zoned THEN r.submitted_at<latest_before
            ELSE (r.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date
            <=nullif(f->>'latest_end','')::date END)
        AND (f->>'pledge_min'='' OR r.annual_pledge>=nullif(f->>'pledge_min','')::numeric)
        AND (f->>'pledge_max'='' OR r.annual_pledge<=nullif(f->>'pledge_max','')::numeric)
        -- "Zero" and "No frequency" mean a Family that pledged nothing, not one
        -- that cannot contribute; that answer has its own filter and count.
        AND (f->>'amount'='any' OR (f->>'amount'='zero' AND r.annual_pledge=0
                AND r.financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb)
            OR (f->>'amount'='nonzero' AND r.annual_pledge>0)
            -- "Cannot contribute financially" (a zero pledge with that answer).
            OR (f->>'amount'='cannot_give' AND r.financial->'cannot_give'='true'::jsonb))
        AND (f->>'frequency'='any'
            OR (f->>'frequency'='none' AND nullif(r.financial->>'frequency','') IS NULL
                AND r.financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb)
            OR r.financial->>'frequency'=f->>'frequency')
        AND (f->>'share'='any'
            OR (f->>'share'='none' AND r.financial->'shares'='{}'::jsonb)
            OR r.financial->'shares' OPERATOR(pg_catalog.?) (f->>'share'))
), page AS MATERIALIZED (
    -- A name sort orders by surname, then the whole shown name, as the
    -- directory does; the Family DUID is unique here, so it ends every order.
    SELECT *,row_number() OVER (ORDER BY
        CASE WHEN f->>'sort'='name' THEN lower(surname) END,
        CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
        CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
        CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
        CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
        CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
        CASE WHEN f->>'sort'='pledge' THEN annual_pledge END,
        CASE WHEN f->>'sort'='pledge_desc' THEN annual_pledge END DESC,
        CASE WHEN f->>'sort'='duid' THEN family_duid END,
        CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,
        lower(surname),lower(display_name),family_duid) AS ordinal
    FROM filtered ORDER BY ordinal
    LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
    OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
), detail AS (
    SELECT r.ordinal,r.id,r.family_name,r.family_duid,r.active,r.submitted_at,
        r.first_submitted_at,r.family_version,r.staff_entered,
        -- Money crosses JSON only as canonical text: a JSON number would be
        -- parsed as a binary float and lose exactness.
        r.annual_pledge::numeric(24,2)::text AS annual_pledge,r.frequency,
        -- Responses recorded before this answer existed read as false.
        coalesce(r.financial->'cannot_give'='true'::jsonb,false) AS cannot_give,
        r.financial->'shares' AS shares,
        -- Whether an Edit is in force (#2196), and the Family's own answer
        -- beside it.
        r.family_answer IS NOT NULL AS edited,r.family_answer,
        -- Option wording is versioned with the configuration the Family saw; the
        -- application words it with the Family form's own rule.
        r.configuration_id,
        g.pledge_total::numeric(24,2)::text AS pledge_total,
        g.contribution_total::numeric(24,2)::text AS contribution_total
    FROM page r CROSS JOIN source x
    LEFT JOIN LATERAL (
        -- Unavailable, never zero, without a proven complete giving read. With
        -- one, a Family with no matching source row really does total zero.
        SELECT CASE WHEN x.giving_through IS NOT NULL THEN coalesce((
                SELECT sum((p.canonical::jsonb->>'amount')::numeric)
                FROM stewardship_snapshot_pledge m
                JOIN stewardship_source_pledge p ON p.id=m.payload_id
                WHERE m.snapshot_id=x.source_id AND p.family_key=r.family_duid::text
                    AND x.values->'financial'->'comparison_fund_duids'
                        OPERATOR(pg_catalog.@>) to_jsonb(p.fund_key::bigint)
                    AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                        (x.values->'financial'->>'comparison_start')::date
                        AND (x.values->'financial'->>'comparison_end')::date),0)
            END AS pledge_total,
            CASE WHEN x.giving_through IS NOT NULL THEN coalesce((
                SELECT sum((p.canonical::jsonb->>'amount')::numeric)
                FROM stewardship_snapshot_contribution m
                JOIN stewardship_source_contribution p ON p.id=m.payload_id
                WHERE m.snapshot_id=x.source_id AND p.family_key=r.family_duid::text
                    AND x.values->'financial'->'comparison_fund_duids'
                        OPERATOR(pg_catalog.@>) to_jsonb(p.fund_key::bigint)
                    AND (p.canonical::jsonb->>'effective_date')::date BETWEEN
                        (x.values->'financial'->>'comparison_start')::date
                        AND least((x.values->'financial'->>'comparison_end')::date,
                            x.giving_through)),0)
            END AS contribution_total
    ) g ON true
)
SELECT CASE WHEN NOT z.values->'modules' OPERATOR(pg_catalog.?) 'financial'
    THEN jsonb_build_object('disabled',true)
    WHEN x.id IS NULL THEN jsonb_build_object('unavailable',true)
    ELSE jsonb_build_object(
    'metadata',jsonb_build_object('id',x.id,'name',x.name,'timezone',x.timezone,
        'source_id',x.source_id,'source_generation',x.source_generation,
        'source_as_of',x.source_as_of,'observed_at',x.observed_at,
        'comparison_start',x.values->'financial'->>'comparison_start',
        'comparison_end',x.values->'financial'->>'comparison_end',
        'giving_through',x.giving_through,'giving_observed_at',x.giving_observed_at),
    'summary',(SELECT jsonb_build_object('families',count(*),
        'annual_total',coalesce(sum(annual_pledge),0)::numeric(24,2)::text,
        'frequencies',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT coalesce(frequency,'none') AS k,count(*) AS n FROM filtered
            WHERE financial->'cannot_give' IS DISTINCT FROM 'true'::jsonb
            GROUP BY 1) d),'{}'::jsonb),
        'shares',coalesce((SELECT jsonb_object_agg(k,n) FROM (
            SELECT s.key AS k,count(*) AS n FROM filtered q
            CROSS JOIN LATERAL jsonb_object_keys(q.financial->'shares') s(key)
            GROUP BY 1) d),'{}'::jsonb),
        'no_share',count(*) FILTER (WHERE financial->'shares'='{}'::jsonb),
        'cannot_give',count(*) FILTER (WHERE financial->'cannot_give'='true'::jsonb))
        FROM filtered),
    'total',(SELECT count(*) FROM filtered),
    'rows',coalesce((SELECT jsonb_agg(to_jsonb(d)-'ordinal' ORDER BY ordinal)
        FROM detail d),'[]'::jsonb)) END
INTO answer FROM selected z LEFT JOIN source x ON true;
    RETURN answer;
END $$;

-- stewardship_information_report_v1: copied from its newest definition (the
-- baseline, information_reports.sql; 0175 last replaced it); only the shown
-- name and the search changed (#2320), and its operators are qualified
-- (#2166).
CREATE OR REPLACE FUNCTION public.stewardship_information_report_v1(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','disposition','needed','completed','start','end','sort']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc','duid','duid_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                -- No name (NULL) for a Family the data no longer has: the
                -- page shows its ParishSoft status badge alone, and it sorts
                -- after the names (#1216). A present Family without a usable
                -- name reads so.
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END)
                AS family_name,
            -- The surname that leads the shown name, as Python names it
            -- (migration 0045's shared functions); family_name above keeps
            -- its own trim for the returned rows.
            stewardship_family_surname_v1(p.canonical::jsonb,
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
            -- Whether surname is the Family's own lastName, not a stand-in for
            -- a blank one (family_names.family_last_name): without one, the
            -- heads' own surnames lead the shown name (#2320).
            stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393). A row loaded under a Families rule names the rule
            -- (campaign_families) and its portal_eligible is "matches the
            -- rule"; a legacy row names none and keeps ParishSoft's active.
            CASE WHEN p.canonical::jsonb OPERATOR(pg_catalog.?) 'campaign_families'
                    AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean
                WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
                THEN (p.canonical::jsonb->'active')::boolean END AS active,
            h.value->'reported' OPERATOR(pg_catalog.?) i.id::text AS previously_reported,
            h.value->'corrected' OPERATOR(pg_catalog.@>)
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          AND (f->>'start'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date>=(f->>'start')::date)
          AND (f->>'end'='' OR (s.submitted_at AT TIME ZONE stewardship_timezone_name_v1(x.timezone))::date<=(f->>'end')::date)
    ), head_names AS MATERIALIZED (
        -- Each Family's active heads as the shown name lists them, in DUID
        -- order (stewardship_family_head_part_v1), so search and sort match
        -- what the page shows (#960). One set-based join over each Family's
        -- heads, skipped when neither a search nor a name sort reads the name.
        SELECT r.family_duid,array_agg(
                stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
                ORDER BY h.head::bigint) AS parts
        FROM (SELECT DISTINCT family_duid,surname,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "Surname, A and B" (family_names.family_heads_name).
        SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
            AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          -- Search matches the shown name, which names every head's
          -- surname, and the Family surname (or its stand-in), which it
          -- may not (#2320).
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(lower(f->>'search') IN lower(surname))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(surname) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
            CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
            CASE WHEN f->>'sort'='duid' THEN family_duid END,
            CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        -- The shown name and heads only order and search; the page and an
        -- export capture keep the row they always had.
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname',
            'surname_recorded','display_name']
            || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

-- stewardship_information_report_v2: copied from its newest definition
-- (migration-owned; 0175 last replaced it); only the shown name and the
-- search changed (#2320), and its operators are qualified (#2166).
CREATE OR REPLACE FUNCTION public.stewardship_information_report_v2(
    campaign uuid, parameters jsonb, page_number integer DEFAULT NULL,
    item_uuid uuid DEFAULT NULL, page_size integer DEFAULT 50
) RETURNS jsonb LANGUAGE plpgsql STABLE
-- JIT compilation costs seconds per call at parish size and saves nothing
-- here, as in the directory report. search_path stays first so the proconfig
-- order matches an ALTER FUNCTION ... SET jit TO off on an installed database.
SET search_path TO pg_catalog,public,pg_temp
SET jit TO off AS $$
DECLARE f jsonb:=parameters->'filters'; answer jsonb;
BEGIN
    IF jsonb_typeof(parameters) IS DISTINCT FROM 'object'
       OR NOT parameters OPERATOR(pg_catalog.?&) ARRAY['filters','history']
       OR parameters-ARRAY['filters','history']<>'{}'::jsonb
       OR jsonb_typeof(parameters->'history') IS DISTINCT FROM 'boolean'
       OR jsonb_typeof(f) IS DISTINCT FROM 'object'
       OR NOT f OPERATOR(pg_catalog.?&) ARRAY['search','disposition','needed','completed','start','end','sort','zone']
       OR f-ARRAY['search','disposition','needed','completed','start','end','sort','zone']<>'{}'::jsonb
       OR EXISTS(SELECT 1 FROM jsonb_each(f) WHERE jsonb_typeof(value)<>'string')
       OR length(f->>'search')>200
       OR f->>'disposition' NOT IN ('current_actionable','superseded','withdrawn','all')
       OR f->>'needed' NOT IN ('any','yes','no')
       OR f->>'completed' NOT IN ('any','yes','no')
       OR f->>'sort' NOT IN ('newest','oldest','name','name_desc','duid','duid_desc')
       OR (page_number IS NOT NULL AND page_number NOT BETWEEN 1 AND 10000)
       OR page_size IS NULL OR page_size NOT BETWEEN 1 AND 100
    THEN RAISE EXCEPTION 'Invalid information report parameters' USING ERRCODE='23514'; END IF;
    -- Python validates the same canonical dates before reaching SQL. The SQL
    -- owner also rejects alternate spellings on direct restricted-role writes.
    IF (f->>'start'<>'' AND (f->>'start')::date::text<>f->>'start')
       OR (f->>'end'<>'' AND (f->>'end')::date::text<>f->>'end')
       OR (f->>'start'<>'' AND f->>'end'<>'' AND f->>'start'>f->>'end')
       -- Days are the viewer's browser zone (#558): required with a date,
       -- and always one the server's time zone catalog knows.
       OR ((f->>'start'<>'' OR f->>'end'<>'') AND f->>'zone'='')
       OR (f->>'zone'<>'' AND NOT EXISTS(SELECT 1 FROM pg_timezone_names WHERE name=stewardship_timezone_name_v1(f->>'zone')))
    THEN RAISE EXCEPTION 'Invalid information report interval' USING ERRCODE='23514'; END IF;

    WITH selected AS MATERIALIZED (
        SELECT c.id,cc.name,cc.timezone,cc.id AS timezone_configuration_id,
            sc.snapshot_id AS source_id,ss.generation AS source_generation,
            ss.promoted_at AS source_as_of,statement_timestamp() AS observed_at
        FROM stewardship_campaign c
        JOIN stewardship_campaign_configuration cc ON cc.id=c.active_configuration_id
        JOIN stewardship_source_current sc ON sc.singleton
        JOIN stewardship_source_snapshot ss ON ss.id=sc.snapshot_id
            AND ss.state='promoted' AND ss.compacted_at IS NULL
        WHERE c.id=campaign
    ), history AS MATERIALIZED (
        SELECT stewardship_weekly_history_v1(id,'production',NULL) AS value FROM selected
    ), rows AS MATERIALIZED (
        SELECT i.id,i.version,i.text,i.disposition,i.replacement_id,
            i.follow_up_needed,i.followed_up_at,r.followed_up_by_id,
            coalesce(u.email,'Former portal user') AS completed_by,
            coalesce(r.notes,'') AS notes,s.submitted_at,fam.family_duid,
            coalesce(nullif(btrim(p.canonical::jsonb->>'lastName'),''),
                nullif(btrim(p.canonical::jsonb->>'mailingName'),''),
                nullif(btrim(concat_ws(' ',
                    nullif(btrim(p.canonical::jsonb->>'firstName'),''),
                    nullif(btrim(p.canonical::jsonb->>'lastName'),''))),''),
                -- No name (NULL) for a Family the data no longer has: the
                -- page shows its ParishSoft status badge alone, and it sorts
                -- after the names (#1216). A present Family without a usable
                -- name reads so.
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END)
                AS family_name,
            -- The surname that leads the shown name, as Python names it
            -- (migration 0045's shared functions); family_name above keeps
            -- its own trim for the returned rows.
            stewardship_family_surname_v1(p.canonical::jsonb,
                CASE WHEN p.id IS NOT NULL THEN 'Name not recorded in ParishSoft' END) AS surname,
            -- Whether surname is the Family's own lastName, not a stand-in for
            -- a blank one (family_names.family_last_name): without one, the
            -- heads' own surnames lead the shown name (#2320).
            stewardship_family_last_name_v1(p.canonical::jsonb)<>'' AS surname_recorded,
            CASE WHEN jsonb_typeof(p.canonical::jsonb->'active_head_duids')='array'
                THEN p.canonical::jsonb->'active_head_duids' END AS head_duids,
            -- Whether the Family is still one of the campaign's Families
            -- (NULL when it is not in the data), for its status badge (#1216,
            -- #1393). A row loaded under a Families rule names the rule
            -- (campaign_families) and its portal_eligible is "matches the
            -- rule"; a legacy row names none and keeps ParishSoft's active.
            CASE WHEN p.canonical::jsonb OPERATOR(pg_catalog.?) 'campaign_families'
                    AND jsonb_typeof(p.canonical::jsonb->'portal_eligible')='boolean'
                THEN (p.canonical::jsonb->'portal_eligible')::boolean
                WHEN jsonb_typeof(p.canonical::jsonb->'active')='boolean'
                THEN (p.canonical::jsonb->'active')::boolean END AS active,
            h.value->'reported' OPERATOR(pg_catalog.?) i.id::text AS previously_reported,
            h.value->'corrected' OPERATOR(pg_catalog.@>)
                jsonb_build_array(jsonb_build_array(i.id::text,i.disposition))
                AS correction_resolved
        FROM selected x CROSS JOIN history h
        JOIN stewardship_submission s ON s.campaign_id=x.id AND s.mode='live'
        JOIN stewardship_additional_information i ON i.submission_id=s.id
        JOIN stewardship_family_campaign fam ON fam.id=s.family_id
        LEFT JOIN stewardship_snapshot_family m
            ON m.snapshot_id=x.source_id AND m.source_key=fam.family_duid::text
        LEFT JOIN stewardship_source_family p ON p.id=m.payload_id
        LEFT JOIN LATERAL (
            SELECT notes,followed_up_by_id FROM stewardship_information_revision
            WHERE item_id=i.id ORDER BY expected_version DESC LIMIT 1) r ON true
        LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
        WHERE (item_uuid IS NULL OR i.id=item_uuid)
          -- From local midnight of the first day to local midnight after the
          -- last, in the viewer's zone; a DST day is 23 or 25 hours long.
          -- Each bound is a scalar subquery, so it is computed once per call.
          AND (f->>'start'='' OR s.submitted_at>=(SELECT
              stewardship_browser_day_start_v1((f->>'start')::date,f->>'zone')))
          AND (f->>'end'='' OR s.submitted_at<(SELECT
              stewardship_browser_day_start_v1((f->>'end')::date+1,f->>'zone')))
    ), head_names AS MATERIALIZED (
        -- Each Family's active heads as the shown name lists them, in DUID
        -- order (stewardship_family_head_part_v1), so search and sort match
        -- what the page shows (#960). One set-based join over each Family's
        -- heads, skipped when neither a search nor a name sort reads the name.
        SELECT r.family_duid,array_agg(
                stewardship_family_head_part_v1(sm.canonical::jsonb,r.surname)
                ORDER BY h.head::bigint) AS parts
        FROM (SELECT DISTINCT family_duid,surname,head_duids FROM rows) r
        CROSS JOIN selected x
        CROSS JOIN LATERAL jsonb_array_elements_text(r.head_duids) h(head)
        JOIN stewardship_snapshot_member mm
            ON mm.snapshot_id=x.source_id AND mm.source_key=h.head
        JOIN stewardship_source_member sm ON sm.id=mm.payload_id
        WHERE f->>'search'<>'' OR f->>'sort' IN ('name','name_desc')
        GROUP BY r.family_duid
    ), named AS MATERIALIZED (
        -- "Surname, A and B" (family_names.family_heads_name).
        SELECT r.*,stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)
            AS display_name
        FROM rows r LEFT JOIN head_names n ON n.family_duid=r.family_duid
    ), filtered AS MATERIALIZED (
        SELECT * FROM named WHERE (f->>'disposition'='all' OR disposition=f->>'disposition')
          AND (f->>'needed'='any' OR follow_up_needed=(f->>'needed'='yes'))
          AND (f->>'completed'='any' OR (followed_up_at IS NOT NULL)=(f->>'completed'='yes'))
          -- Search matches the shown name, which names every head's
          -- surname, and the Family surname (or its stand-in), which it
          -- may not (#2320).
          AND (f->>'search'='' OR position(lower(f->>'search') IN lower(display_name))>0
            OR position(lower(f->>'search') IN lower(surname))>0
            OR position(f->>'search' IN family_duid::text)>0
            OR position(lower(f->>'search') IN lower(text))>0
            OR position(lower(f->>'search') IN lower(notes))>0)
    ), ordered AS (
        -- A name sort orders by surname, then the whole shown name, as the
        -- directory does; the item id is the unique tiebreak.
        SELECT *,row_number() OVER (ORDER BY
            CASE WHEN f->>'sort'='newest' THEN submitted_at END DESC,
            CASE WHEN f->>'sort'='oldest' THEN submitted_at END,
            CASE WHEN f->>'sort'='name' THEN lower(surname) END,
            CASE WHEN f->>'sort'='name' THEN lower(display_name) END,
            CASE WHEN f->>'sort'='name_desc' THEN lower(surname) END DESC,
            CASE WHEN f->>'sort'='name_desc' THEN lower(display_name) END DESC,
            CASE WHEN f->>'sort'='duid' THEN family_duid END,
            CASE WHEN f->>'sort'='duid_desc' THEN family_duid END DESC,id) AS ordinal
        FROM filtered
    ), page AS (
        SELECT * FROM ordered ORDER BY ordinal
        LIMIT CASE WHEN page_number IS NULL THEN NULL ELSE page_size END
        OFFSET CASE WHEN page_number IS NULL THEN 0 ELSE (page_number-1)*page_size END
    ), detached AS (
        -- The shown name and heads only order and search; the page and an
        -- export capture keep the row they always had.
        SELECT ordinal,to_jsonb(page)-ARRAY['ordinal','head_duids','surname',
            'surname_recorded','display_name']
            || jsonb_build_object('history',
            CASE WHEN (parameters->>'history')::boolean THEN coalesce((
                SELECT jsonb_agg(jsonb_build_object(
                    'version',r.expected_version+1,'created_at',r.created_at,
                    'actor_id',r.actor_id,'actor_label',coalesce(a.email,'Former portal user'),
                    'follow_up_needed',r.follow_up_needed,'followed_up',r.followed_up,
                    'followed_up_at',r.followed_up_at,'followed_up_by_id',r.followed_up_by_id,
                    'completed_by',coalesce(u.email,'Former portal user'),'notes',r.notes)
                    ORDER BY r.expected_version)
                FROM stewardship_information_revision r
                LEFT JOIN stewardship_portal_user a ON a.id=r.actor_id
                LEFT JOIN stewardship_portal_user u ON u.id=r.followed_up_by_id
                WHERE r.item_id=page.id AND r.expected_version<page.version
            ),'[]'::jsonb) ELSE '[]'::jsonb END) AS value FROM page
    )
    SELECT jsonb_build_object('metadata',to_jsonb(x),
        'total',(SELECT count(*) FROM filtered),
        -- Every matching request by its follow-up state, for the page's
        -- Follow-up status chart (#720): completed, or not yet completed
        -- with follow-up marked needed or not. The three add up to total.
        'counts',(SELECT jsonb_build_object(
            'completed',count(*) FILTER (WHERE followed_up_at IS NOT NULL),
            'needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND follow_up_needed),
            'not_needed_open',count(*) FILTER (WHERE followed_up_at IS NULL AND NOT follow_up_needed))
            FROM filtered),
        'rows',coalesce((SELECT jsonb_agg(value ORDER BY ordinal) FROM detached),'[]'::jsonb))
    INTO answer FROM selected x;
    RETURN answer;
END $$;

DO $check$
BEGIN
    -- The name functions are installed once, as declared: invokers with the
    -- pinned search_path, immutable, parallel safe, returning text, with the
    -- default grants, and the replaced ones carry the new bodies. The
    -- display name lookup is the same but stable.
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
            OR (p.proname='stewardship_family_last_name_v1'
                AND pg_get_function_identity_arguments(p.oid)='family jsonb')
            OR (p.proname='stewardship_family_heads_name_v2'
                AND pg_get_function_identity_arguments(p.oid)=
                    'surname text, recorded boolean, parts text[]'
                AND p.prosrc LIKE '%recorded AND h.key=casefold(surname COLLATE pg_catalog.pg_unicode_fast)%')
            OR (p.proname='stewardship_family_heads_name_v1'
                AND pg_get_function_identity_arguments(p.oid)='surname text, parts text[]'
                AND p.prosrc LIKE '%stewardship_family_heads_name_v2(surname,true,parts)%')))<>5
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname='stewardship_family_display_name_v1'
             AND NOT p.prosecdef AND p.proacl IS NULL
             AND p.proparallel='s' AND p.provolatile='s'
             AND p.proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND p.prorettype='text'::regtype
             AND pg_get_function_identity_arguments(p.oid)='snapshot_id uuid, duid bigint, fallback text'
             AND p.prosrc LIKE '%stewardship_family_last_name_v1(f.family)<>%')<>1
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_name_series_v1',
               'stewardship_family_head_part_v1','stewardship_family_heads_name_v1',
               'stewardship_family_last_name_v1','stewardship_family_heads_name_v2',
               'stewardship_family_display_name_v1'))<>6
    THEN
        RAISE EXCEPTION 'Migration 0194 (mixed-surname Family names) is not installed as declared';
    END IF;
    -- They name the issue's Families as family_names.family_heads_name does:
    -- two surnames, a surname group of two, every head sharing the surname,
    -- none sharing it, letter case, a head without a last name, an inactive
    -- or missing head, no heads, and a blank lastName (a stand-in surname,
    -- even one equal to a head's).
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
       OR stewardship_family_last_name_v1('{"lastName":" Lee\u00a0"}') IS DISTINCT FROM 'Lee'
       OR stewardship_family_last_name_v1('{"lastName":7,"mailingName":"Lee"}') IS DISTINCT FROM ''
       OR stewardship_family_last_name_v1(NULL) IS DISTINCT FROM ''
       OR stewardship_family_heads_name_v2('The Lees',false,ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','The Lees'),
           stewardship_family_head_part_v1('{"firstName":"Bo","lastName":"Lee","active":true}','The Lees')])
           IS DISTINCT FROM 'Lee, Ann and Bo'
       OR stewardship_family_heads_name_v2('Fox',false,ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','Fox'),
           stewardship_family_head_part_v1('{"firstName":"Bo","lastName":"fox","active":true}','Fox')])
           IS DISTINCT FROM 'Lee / fox, Ann / Bo'
       OR stewardship_family_heads_name_v2('Lee',false,ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','Lee'),
           stewardship_family_head_part_v1('{"firstName":"Cher","active":true}','Lee')])
           IS DISTINCT FROM 'Lee, Ann / Cher'
       OR stewardship_family_heads_name_v2('The Lees',false,ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","active":true}','The Lees'),
           stewardship_family_head_part_v1('{"firstName":"Bo","lastName":" ","active":true}','The Lees')])
           IS DISTINCT FROM 'The Lees, Ann and Bo'
       OR stewardship_family_heads_name_v2('Pat',false,ARRAY[]::text[]) IS DISTINCT FROM 'Pat'
       OR stewardship_family_heads_name_v2('Fox',true,ARRAY[
           stewardship_family_head_part_v1('{"firstName":"Ann","lastName":"Lee","active":true}','Fox'),
           stewardship_family_head_part_v1('{"firstName":"Bo","lastName":"fox","active":true}','Fox')])
           IS DISTINCT FROM 'Fox / Lee, Bo / Ann'
    THEN
        RAISE EXCEPTION 'Migration 0194 Family name functions do not name Families as Python does';
    END IF;
    -- Both directory selections, once, invoker, stable, build the shown name
    -- with the shared function, told whether the surname is recorded, and
    -- no longer inline.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_directory_report_v1','stewardship_directory_report_v2')
          AND pg_get_function_identity_arguments(p.oid)=
              'campaign_uuid uuid, parameters jsonb, page_number integer'
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
          AND p.prosrc LIKE '%stewardship_family_heads_name_v2(family_name,surname_recorded,n.parts) AS display_name%'
          AND p.prosrc LIKE '%IS NOT NULL%AS surname_recorded,%'
          AND p.prosrc LIKE '%ELSE jsonb_build_array(t.first,t.last)::text END) p(part)%'
          AND p.prosrc NOT LIKE '%t.last=b.family_name%')<>2
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_directory_report_v1',
               'stewardship_directory_report_v2'))<>2 THEN
        RAISE EXCEPTION 'Migration 0194 (mixed-surname Family names) did not replace the directory selections';
    END IF;
    -- The Financial and both Additional information selections, once,
    -- invoker, stable, name the Family with the recorded flag and search
    -- the Family surname too.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public'
          AND p.proname IN ('stewardship_financial_report_v1','stewardship_information_report_v1',
              'stewardship_information_report_v2')
          AND NOT p.prosecdef AND p.provolatile='s' AND p.prorettype='jsonb'::regtype
          AND p.prosrc LIKE '%stewardship_family_last_name_v1(p.canonical::jsonb)<>% AS surname_recorded,%'
          AND p.prosrc LIKE '%stewardship_family_heads_name_v2(r.surname,r.surname_recorded,n.parts)%'
          AND p.prosrc LIKE '%OR position(lower(f->>''search'') IN lower(%surname))>0%'
          AND p.prosrc NOT LIKE '%stewardship_family_heads_name_v1(%')<>3
       OR (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN ('stewardship_financial_report_v1',
               'stewardship_information_report_v1','stewardship_information_report_v2'))<>3 THEN
        RAISE EXCEPTION 'Migration 0194 (mixed-surname Family names) did not replace the report selections';
    END IF;
    -- CREATE OR REPLACE keeps the grants and names the same settings; check
    -- that nothing changed them.
    IF (SELECT count(*)||':'||coalesce(string_agg(p.proname||'='||coalesce(p.proacl::text,'default')
                ||'/'||coalesce((SELECT array_agg(c ORDER BY c) FROM unnest(p.proconfig) c)::text,''),';'
            ORDER BY p.proname),'')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname IN ('stewardship_family_head_part_v1',
            'stewardship_family_heads_name_v1','stewardship_family_display_name_v1',
            'stewardship_directory_report_v1','stewardship_directory_report_v2',
            'stewardship_financial_report_v1','stewardship_information_report_v1',
            'stewardship_information_report_v2'))
        IS DISTINCT FROM current_setting('stewardship.migration_0194_acl', true) THEN
        RAISE EXCEPTION 'Migration 0194 changed the replaced functions'' grants or settings';
    END IF;
END
$check$;
