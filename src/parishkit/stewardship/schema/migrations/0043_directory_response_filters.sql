-- Frozen forward migration file 0043 (the repository-wide file sequence):
-- the active parishioner family directory gains the response lists' filters
-- (#933, slices 2 and 3). This file is installed by its Django migration in
-- the reports app and must never change once released;
-- tests/stewardship/test_schema_migration_files.py pins its digest and checks
-- that its copies of the replaced functions equal the fresh-install
-- baseline's (schema/directory_reports.sql and schema/functions.sql). A
-- fresh install runs the baseline, the earlier files and then this; the
-- baseline already carries the replaced bodies, so the install ends in the
-- same catalog as an upgraded database.
--
-- The Response dashboard, its lists and a daily digest count the response
-- funnel from one statement in Python (reports.response_metrics). The
-- directory orders and pages in SQL, so it cannot filter on that statement's
-- rows from Python. The statement moves into the database, unchanged, so the
-- dashboard and the directory read one definition and cannot drift:
--   stewardship_family_response_v1   new: one row per Family of the campaign
--                                    with its funnel instants at an as-of
--                                    instant, in Production or one Testing
--                                    rehearsal (the former inline statement)
--   stewardship_directory_report_v2  new: v1's selection plus the Response
--                                    and ParishSoft data to check filters,
--                                    the response columns and their sort
--                                    orders; under a Response filter it
--                                    also lists the campaign's Families that
--                                    are no longer active, marked so
--   stewardship_directory_export_capture_v1
--                                    replaced: 0021's body, capturing through
--                                    v2, and keeping the envelope number in
--                                    the Family-code file (not the mail merge)
--   stewardship_safe_context_v1      replaced: 0039's body plus the
--                                    directory's new closed audit values
--                                    (directory_response choices,
--                                    directory_data_check,
--                                    directory_response_columns and the new
--                                    directory_sort orders)
-- stewardship_directory_report_v1 stays as it is; nothing in the application
-- calls it after this release. Every function here is an invoker function
-- (not SECURITY DEFINER) with a fixed search_path, executable as every other
-- invoker function is: each reads only what its caller's grants allow. The
-- web login (the pages and export captures) and the background worker (the
-- daily digest's funnel) already read every table the funnel reads. The
-- replaced functions keep their owners, grants and attributes; the DO block
-- at the end checks them. Reversing needs its own forward migration.
SET LOCAL check_function_bodies = false;
SET LOCAL search_path = public;

-- The replaced functions' grants before the replacement, compared in the DO
-- block. A transaction-local setting holds them, not a temporary table: the
-- migration login has no TEMP privilege.
SELECT set_config('stewardship.migration_0043_acl', (
    SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
    FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
    WHERE n.nspname='public' AND p.proname IN
        ('stewardship_directory_export_capture_v1','stewardship_safe_context_v1')), true);

-- One row per Family of the campaign, with each funnel instant as it stood at
-- as_of (the Response dashboard's rows, reports.response_metrics): the first
-- delivered invitation, whether a planned invitation was skipped because the
-- Family had already responded, the engagement record's first instants, and
-- the first and last submission with how many there were. Mode is the
-- system's: 'production' reads live responses and Production mail with no
-- rehearsal epoch; 'testing' reads one rehearsal epoch's responses,
-- engagement and messages, and the occurrences and skips that fell within the
-- epoch's lifetime (occurrences carry no epoch). Each source is aggregated
-- once per campaign and joined to the Family rows (hash joins at launch
-- scale, about 1,100 Families), never per Family. The body is the former
-- inline statement, unchanged but for its parameters.
CREATE FUNCTION public.stewardship_family_response_v1(
    campaign_uuid uuid, report_mode text, epoch_uuid uuid, as_of timestamptz
) RETURNS TABLE(family_id uuid, family_duid bigint, invited_at timestamptz,
    skipped_responded boolean, link_at timestamptz, form_at timestamptz,
    progress_at timestamptz, submitted_at timestamptz, submissions bigint,
    last_submitted_at timestamptz)
LANGUAGE plpgsql STABLE
SET search_path TO pg_catalog,public,pg_temp AS $$
#variable_conflict use_column
DECLARE response_mode text; mail_mode text;
BEGIN
    IF campaign_uuid IS NULL OR as_of IS NULL
       OR report_mode IS NULL OR report_mode NOT IN ('production','testing')
       OR (report_mode='testing')<>(epoch_uuid IS NOT NULL)
    THEN RAISE EXCEPTION 'Invalid response funnel parameters' USING ERRCODE='23514'; END IF;
    -- How responses and engagement spell the mode, and how mail spells it.
    response_mode:=CASE report_mode WHEN 'production' THEN 'live' ELSE 'test' END;
    mail_mode:=report_mode;
    RETURN QUERY
    WITH lifetime AS (
        -- The instants the scope covers: a Testing scope's epoch lifetime
        -- (from its creation until it was invalidated); every instant for
        -- Production.
        SELECT e.created_at AS started_at,
            coalesce(e.invalidated_at,'infinity'::timestamptz) AS ended_at
        FROM stewardship_rehearsal_epoch e WHERE e.id=epoch_uuid
        UNION ALL
        SELECT '-infinity'::timestamptz,'infinity'::timestamptz
        WHERE epoch_uuid IS NULL
    ), invited AS (
        SELECT m.family_id,min(m.finished_at) AS at
        FROM stewardship_outbox_message m
        WHERE m.campaign_id=campaign_uuid AND m.purpose='initial'
          AND m.mode=mail_mode
          AND m.rehearsal_epoch_id IS NOT DISTINCT FROM epoch_uuid
          AND m.state='delivered' AND m.finished_at<=as_of
        GROUP BY m.family_id
    ), skipped AS (
        SELECT DISTINCT o.target
        FROM stewardship_schedule_definition d
        JOIN stewardship_schedule_occurrence o ON o.definition_id=d.id
        JOIN stewardship_occurrence_transition t ON t.occurrence_id=o.id
        JOIN lifetime l ON t.created_at>=l.started_at AND t.created_at<l.ended_at
        WHERE d.campaign_id=campaign_uuid AND d.kind='initial'
          AND o.mode=mail_mode
          AND t.after_state='skipped' AND t.reason='family_responded'
          AND t.created_at<=as_of
    ), responded AS (
        SELECT s.family_id,min(s.submitted_at) AS at,count(*) AS submissions,
            max(s.submitted_at) AS last_at
        FROM stewardship_submission s
        WHERE s.campaign_id=campaign_uuid AND s.mode=response_mode
          AND s.rehearsal_epoch_id IS NOT DISTINCT FROM epoch_uuid
          AND s.submitted_at<=as_of
        GROUP BY s.family_id
    )
    SELECT f.id,f.family_duid,i.at,k.target IS NOT NULL,
        CASE WHEN e.first_link_at<=as_of THEN e.first_link_at END,
        CASE WHEN e.first_form_at<=as_of THEN e.first_form_at END,
        CASE WHEN e.first_progress_at<=as_of THEN e.first_progress_at END,
        r.at,coalesce(r.submissions,0),r.last_at
    FROM stewardship_family_campaign f
    LEFT JOIN invited i ON i.family_id=f.id
    LEFT JOIN skipped k ON k.target='family:'||f.id::text
    LEFT JOIN stewardship_family_engagement e ON e.family_id=f.id
        AND e.mode=response_mode
        AND e.rehearsal_epoch_id IS NOT DISTINCT FROM epoch_uuid
    LEFT JOIN responded r ON r.family_id=f.id
    WHERE f.campaign_id=campaign_uuid
    ORDER BY f.family_duid,f.id;
END $$;

-- The active parishioner family directory's one selection, for its page, its
-- export captures and the header's Find a Family box: v1's selection plus
-- the response lists' filters (#933). The parameters v1 takes keep their
-- meaning; the filter 'response' adds the Response choices below to v1's
-- yes and no. New, all optional:
--   responses   boolean; the page shows the response columns
--   mode        'production' (the default) or 'testing'
--   epoch       the Testing rehearsal epoch; null in Production
--   filters.check   ParishSoft data to check: 'any' (the default),
--               'anything', 'mailing-name' (blank mailing name) or
--               'envelope' (envelope number 0)
-- The Response choices select rows by stewardship_family_response_v1, the
-- Response dashboard's own rows, counted at this statement's start
-- ('counted_at' in the metadata): 'submitted', 'more-than-once', 'started'
-- (form opened, nothing submitted), 'progressed' and 'opened-only' (the two
-- halves of started), 'never-opened' (invited, form never opened) with its
-- halves 'link-followed' and 'link-not-followed', 'not-invited' (no
-- delivered invitation and form never opened), and 'not-submitted' (nothing
-- submitted). For the active Families with a campaign record, 'started',
-- 'never-opened' and 'not-invited' split 'not-submitted' with no overlap.
-- 'not-submitted' and 'not-invited' list active Families only.
-- 'not-submitted' includes active Families with no campaign record (nothing
-- delivered or submitted for them); 'not-invited' is a response status, not
-- a postal list, and leaves them out (#951). v1's 'yes'
-- and 'no' still work, so an application from before v2 can still capture
-- exports through it: they read as 'submitted' and 'not-submitted', active
-- Families only. Under every other choice except 'any' the rows are every
-- campaign Family the funnel selects, so a list's length equals its
-- dashboard tile: a Family the
-- current ParishSoft data no longer lists as active and registered is listed
-- too, with active false, reason 'inactive', no email or postal reach, and
-- its name and envelope from the current data when the data still has it
-- (family_name null otherwise). With 'any' the rows are the active
-- Families, as in v1. Each row carries its funnel instants (null when the
-- funnel was not needed: no Response filter, response columns or response
-- order) and its two data checks.
CREATE FUNCTION public.stewardship_directory_report_v2(
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
       OR NOT parameters ?& ARRAY['filters','postal','exact','family_id']
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
       OR NOT f ?& ARRAY['search','reason','phone','response','sort']
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
            'Family') END AS family_name,
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
    SELECT b.*,CASE WHEN NOT active THEN 'inactive'
        WHEN email_deliverable THEN 'deliverable'
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

-- The export capture, exactly as the fresh-install directory_reports.sql
-- defines it: 0021's body, capturing through v2 and keeping the envelope
-- number for the Family-code file. CREATE OR REPLACE resets every attribute
-- it does not state; this states the baseline's own (invoker rights, the
-- fixed search_path). The trigger and its grants are unchanged.
CREATE OR REPLACE FUNCTION public.stewardship_directory_export_capture_v1() RETURNS trigger
LANGUAGE plpgsql SET search_path TO pg_catalog,public,pg_temp AS $$
DECLARE postal boolean; phones boolean;
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'Directory export snapshots are immutable' USING ERRCODE='23514';
    END IF;
    PERFORM stewardship_export_campaign_lock_v1(NEW.campaign_id,false);
    IF NOT stewardship_export_authorized_v1(NEW.actor_id)
       OR NOT stewardship_export_admitted_v1(NEW.campaign_id,true)
       OR current_user='pk_stewardship_worker'
       OR NOT EXISTS(SELECT 1 FROM stewardship_system_configuration
           WHERE active_configuration_id=NEW.configuration_id)
    THEN RAISE EXCEPTION 'Directory export capture is unavailable' USING ERRCODE='23514'; END IF;
    NEW.created_at:=statement_timestamp();
    NEW.document:=stewardship_directory_report_v2(NEW.campaign_id,NEW.parameters);
    IF NEW.document IS NULL THEN
        RAISE EXCEPTION 'Directory export inputs are unavailable' USING ERRCODE='23514';
    END IF;
    NEW.source_id:=(NEW.document->'metadata'->>'source_id')::uuid;
    NEW.row_count:=(NEW.document->>'total')::integer;
    -- Keep only the private contact columns the requested file renders
    -- (#388 L6; reports.directory_documents): the postal mail merge uses the
    -- address, and the code list uses phones only for reach 'neither' and the
    -- envelope number (#933). The keys stay, emptied, so the document keeps
    -- its shape; heads (names and head emails) are in both.
    postal:=coalesce((NEW.parameters->>'postal')::boolean,false);
    phones:=NOT postal AND NEW.parameters->'filters'->>'reach' IS NOT DISTINCT FROM 'neither';
    NEW.document:=jsonb_set(NEW.document,'{rows}',coalesce((SELECT jsonb_agg(
        e.r||CASE WHEN postal THEN jsonb_build_object('envelope',NULL) ELSE '{}'::jsonb END
           ||CASE WHEN postal THEN '{}'::jsonb ELSE jsonb_build_object('address','{}'::jsonb) END
           ||CASE WHEN phones THEN '{}'::jsonb ELSE jsonb_build_object('phones','[]'::jsonb) END
        ORDER BY e.ordinal)
        FROM jsonb_array_elements(NEW.document->'rows') WITH ORDINALITY e(r,ordinal)),
        '[]'::jsonb));
    RETURN NEW;
END $$;

-- The audit context check, 0039's body plus the directory's new values. It
-- keeps its identity and attributes (IMMUTABLE, its search_path, not
-- SECURITY DEFINER).
CREATE OR REPLACE FUNCTION public.stewardship_safe_context_v1(schema_name text, payload jsonb) RETURNS boolean
    LANGUAGE plpgsql IMMUTABLE
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $_$
DECLARE allowed text[]; key text; value jsonb; text_value text;
        ministry jsonb; previous_ministry bigint;
BEGIN
    allowed=CASE schema_name
        WHEN 'request' THEN ARRAY['method','status','outcome','source_fingerprint']
        WHEN 'task' THEN ARRAY['task_id','count','version','outcome']
        WHEN 'email' THEN ARRAY['message_id','recipient_count','outcome','reason']
        WHEN 'source' THEN ARRAY['snapshot_id','generation','count','outcome']
        WHEN 'member_source' THEN ARRAY['family_duid','member_duid','field']
        WHEN 'provider' THEN ARRAY['status','provider_fingerprint','outcome']
        WHEN 'exception' THEN ARRAY['outcome','retryable']
        WHEN 'action' THEN ARRAY['version','before_version','after_version','outcome','source_fingerprint','candidate_fingerprint','count',
            'matching_count','page','directory_reason','directory_phone','directory_response','directory_sort','directory_reach','search_used','exact_code_used','ministry_duid','ministry_duids','ministry_operational',
            'previous_ministry_duids','added_ministry_duids','removed_ministry_duids',
            'decision','review_reason','file_slug','previous_file_slug','file_kind','file_size','file_fingerprint',
            'report_mode','report_filter','talent_option_id','snapshot_id',
            'report_sort','directory_data_check','directory_response_columns']
        WHEN 'boundary' THEN ARRAY['occurrence_id','kind','intended_unix_microseconds','actual_unix_microseconds','lag_microseconds','before_state','after_state']
        WHEN 'schedule' THEN ARRAY['definition_id','previous_revision_id','selected_revision_id','cancelled_messages','skipped_occurrences','failed_occurrences','delivered_slots']
        WHEN 'timeout' THEN ARRAY['task_id','task_type','attempt','limit_seconds','elapsed_seconds','what','helper','count','outcome']
        -- What made scheduled work late (#634): the task type, how many tasks
        -- and how late against which limit, or the Family send that stalled
        -- or overran, with its counts, how long since its last progress and
        -- how many other tasks were late in the same check.
        WHEN 'due_work' THEN ARRAY['task_type','count','lag_seconds','limit_seconds','definition_id','revision_id',
            'remaining_count','done_count','stall_seconds','elapsed_seconds','other_late_count',
            'occurrence_id','task_id']
        -- What failed and what happens next (#633): a closed word for what
        -- failed, the exception's category, the task, message and attempt,
        -- a closed provider reason or HTTP status, and whether it retries.
        WHEN 'failure' THEN ARRAY['failure','failure_kind','task_id','task_type','message_id','version',
            'attempt','attempt_limit','retry_seconds','status','reason','count','outcome','command']
        -- An operational incident that ended (#633): the incident, its kind,
        -- the entry that opened it, how long it lasted and how often it was seen.
        WHEN 'recovery' THEN ARRAY['incident_id','incident_kind','log_id','elapsed_seconds','count']
        ELSE NULL END;
    IF allowed IS NULL OR jsonb_typeof(payload) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
    IF schema_name IN ('member_source','boundary') AND NOT payload ?& allowed THEN RETURN false; END IF;
    FOR key,value IN SELECT * FROM jsonb_each(payload) LOOP
        IF NOT key=ANY(allowed) THEN RETURN false; END IF;
        text_value=value#>>'{}';
        IF key='outcome' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('started','succeeded','denied','failed','retry','cancelled','changed') THEN RETURN false; END IF;
        ELSIF key='reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('no_deliverable_recipient',
                'smtp_transient','smtp_unavailable','smtp_permanent','smtp_systemic','smtp_delivery_unknown',
                'preparation_failed','slack_not_sent','slack_delivery_unknown') THEN RETURN false; END IF;
        -- What failed (#633): a closed word (audit.schemas.FAILURES).
        ELSIF key='failure' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('organization_mismatch','destructive_change',
                'shifted_scan','invalid_payload','incomplete_collection','invalid_response','lease_unavailable',
                'configuration_activating','configuration_busy','scope_changed','credential_unreadable',
                'credential_changed','provider_status','provider_timeout','provider_unreachable',
                'source_configuration','organization_changed','source_health_check','mail_health_check',
                'due_work_health_check','backup_health_check','export_cleanup','fact_verification',
                'source_retention','family_engagement','alert_mail','security_mail','slack_alert',
                'smtp_systemic','smtp_unavailable','web_health_check','web_unresponsive',
                'admin_command','admin_command_outcome_unknown') THEN RETURN false; END IF;
        -- An Admin command-line command (#617): its catalog name, lower-case
        -- words such as 'export create' (Python checks the catalog itself).
        ELSIF key='command' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z][a-z-]*( [a-z][a-z-]*){0,3}$' THEN RETURN false; END IF;
        -- A failure's category and an incident's kind: identifier words whose
        -- closed sets Python owns (observability.FailureKind, IncidentKind).
        ELSIF key IN ('failure_kind','incident_kind') THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('start','close') THEN RETURN false; END IF;
        ELSIF key IN ('before_state','after_state') THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('draft','scheduled','active','closed','archived',
                'purging','purge_cleanup_failed','purged') THEN RETURN false; END IF;
        ELSIF key='field' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN (
                'prefix','first_name','middle_name','last_name','suffix','nickname',
                'maiden_name','birth_date','gender','email','home_phone',
                'mobile_phone','work_phone','marital_status','language','death_date'
            ) THEN RETURN false; END IF;
        ELSIF key IN ('family_duid','member_duid','ministry_duid') THEN
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,10}$' THEN RETURN false; END IF;
            IF text_value::numeric NOT BETWEEN 1 AND 2147483647 THEN RETURN false; END IF;
        ELSIF key IN ('ministry_duids','previous_ministry_duids','added_ministry_duids','removed_ministry_duids') THEN
            IF jsonb_typeof(value)<>'array' THEN RETURN false; END IF;
            previous_ministry:=0;
            FOR ministry IN SELECT * FROM jsonb_array_elements(value) LOOP
                IF jsonb_typeof(ministry)<>'number' OR ministry#>>'{}'!~'^[0-9]{1,10}$'
                THEN RETURN false; END IF;
                IF (ministry#>>'{}')::bigint NOT BETWEEN previous_ministry+1 AND 2147483647
                THEN RETURN false; END IF;
                previous_ministry:=(ministry#>>'{}')::bigint;
            END LOOP;
        ELSIF key='method' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('GET','HEAD','POST') THEN RETURN false; END IF;
        -- Work stopped by a time limit (#293): the task type and the limit.
        ELSIF key='task_type' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[a-z][a-z0-9_]{0,63}$' THEN RETURN false; END IF;
        ELSIF key='what' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('read_guard','lease','retention_budget','drive_copy_budget',
                'drive_retry_budget','drive_request','drive_probe_wait',
                'statement_timeout','lock_timeout','transaction_timeout','mail_helper','source_helper','provider_check',
                'renewal_drain','control_lock','web_drain','web_heartbeat','configuration_activation','web_probe','source_load_budget') THEN RETURN false; END IF;
        ELSIF key='helper' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('readiness_delivery_worker','readiness_notification_worker',
                'family_delivery_worker','digest_delivery_worker','weekly_delivery_worker','operational_mail_worker',
                'operational_slack_worker','security_mail_worker','provider_check_worker','parishsoft_http_worker') THEN RETURN false; END IF;
        -- An Administrator's decision on a suspended Chairperson seed: a closed
        -- word, and the reason entered for it, bounded text refused when it
        -- carries an address-like token, since this context holds no
        -- personal data.
        ELSIF key='decision' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('keep_role','restore','remove') THEN RETURN false; END IF;
        ELSIF key='review_reason' THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value) NOT BETWEEN 1 AND 500
               OR position('@' in text_value)>0 THEN RETURN false; END IF;
        -- A hosted file (#346): its placeholder name and its stored type.
        ELSIF key IN ('file_slug','previous_file_slug') THEN
            IF jsonb_typeof(value)<>'string' OR length(text_value)>64
               OR text_value!~'^[a-z0-9]+(-[a-z0-9]+)*$' THEN RETURN false; END IF;
        ELSIF key='file_kind' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('pdf','docx','xlsx','pptx','png','jpeg') THEN RETURN false; END IF;
        -- An on-request report page or download (#556): the system mode it
        -- showed and its closed filter choice (audit.schemas.REPORT_FILTERS).
        ELSIF key='report_mode' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('production','testing') THEN RETURN false; END IF;
        ELSIF key='report_filter' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('all','invited','uninvited','progressed',
                'opened','followed','unfollowed','mailing-name','envelope','any','cannot_serve',
                'cannot_attend','option') THEN RETURN false; END IF;
        -- A response list's sort order (#851): its closed sort token, a column
        -- key ascending or '-key' descending (audit.schemas.REPORT_SORTS).
        ELSIF key='report_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('family','-family',
                'duid','-duid','envelope','-envelope','submitted','-submitted',
                'submissions','-submissions','opened','-opened','progressed','-progressed',
                'invited','-invited','link','-link','last','-last','mailing','-mailing',
                'problem','-problem') THEN RETURN false; END IF;
        ELSIF key LIKE '%\_id' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN RETURN false; END IF;
        ELSIF key LIKE '%\_fingerprint' ESCAPE '\' THEN
            IF jsonb_typeof(value)<>'string' OR text_value!~'^[0-9a-f]{64}$' THEN RETURN false; END IF;
        ELSIF key IN ('retryable','search_used','exact_code_used','ministry_operational','directory_response_columns') THEN
            IF jsonb_typeof(value)<>'boolean' THEN RETURN false; END IF;
        ELSIF key='directory_reason' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','no_head','no_address','invalid_address','provider_refused','deliverable') THEN RETURN false; END IF;
        ELSIF key='directory_phone' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no') THEN RETURN false; END IF;
        -- The directory's Response filter (#933): its closed choices, plus the
        -- older yes and no that earlier entries recorded.
        ELSIF key='directory_response' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','yes','no','submitted',
                'more-than-once','started','progressed','opened-only','never-opened',
                'link-followed','link-not-followed','not-invited','not-submitted') THEN RETURN false; END IF;
        -- The directory's ParishSoft data to check filter (#933).
        ELSIF key='directory_data_check' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','anything','mailing-name','envelope') THEN RETURN false; END IF;
        ELSIF key='directory_sort' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('name','name_desc','duid','duid_desc',
                'invited','invited_desc','link','link_desc','opened','opened_desc',
                'progressed','progressed_desc','submitted','submitted_desc','last','last_desc',
                'submissions','submissions_desc') THEN RETURN false; END IF;
        -- How campaign mail can reach the listed Families (#388 L1).
        ELSIF key='directory_reach' THEN
            IF jsonb_typeof(value)<>'string' OR text_value NOT IN ('any','email','mail','neither') THEN RETURN false; END IF;
        ELSE
            IF jsonb_typeof(value)<>'number' OR text_value!~'^[0-9]{1,19}$' THEN RETURN false; END IF;
            IF text_value::numeric>9223372036854775807 THEN RETURN false; END IF;
            IF key='status' AND text_value::numeric NOT BETWEEN 100 AND 599 THEN RETURN false; END IF;
        END IF;
    END LOOP;
    RETURN true;
END $_$;

-- Refuse to commit unless every function is installed as declared: the new
-- ones with their signatures and attributes, the replaced ones with their
-- new bodies, their attributes and their grants as before, and the capture
-- trigger still calling its function. No temporary objects: the migration
-- login has no TEMP grant.
DO $check$
BEGIN
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_family_response_v1')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_family_response_v1(uuid,text,uuid,timestamptz)'::regprocedure
             AND NOT prosecdef AND provolatile='s' AND proretset
             AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%t.reason=''family_responded''%') THEN
        RAISE EXCEPTION 'stewardship_family_response_v1 is not installed as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v2')<>1
       OR NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_directory_report_v2(uuid,jsonb,integer)'::regprocedure
             AND NOT prosecdef AND provolatile='s' AND prorettype='jsonb'::regtype
             AND proconfig @> ARRAY['jit=off','search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%stewardship_family_response_v1(campaign_uuid,report_mode,epoch_uuid,counted)%'
             AND prosrc LIKE '%-- BEGIN DIRECTORY SELECTION%'
             AND prosrc LIKE '%WHEN ''not-invited'' THEN active AND family_id IS NOT NULL%'
             AND prosrc LIKE '%WHEN o.f->>''sort''=''duid_desc'' THEN family_duid END DESC%') THEN
        RAISE EXCEPTION 'stewardship_directory_report_v2 is not installed as declared';
    END IF;
    -- v1 is left as it was.
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_directory_report_v1')<>1 THEN
        RAISE EXCEPTION 'stewardship_directory_report_v1 must still have exactly one definition';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_proc
           WHERE oid='public.stewardship_directory_export_capture_v1()'::regprocedure
             AND NOT prosecdef
             AND proconfig=ARRAY['search_path=pg_catalog, public, pg_temp']
             AND prosrc LIKE '%stewardship_directory_report_v2(NEW.campaign_id,NEW.parameters)%'
             AND prosrc LIKE '%CASE WHEN postal THEN jsonb_build_object(''envelope'',NULL)%')
       OR NOT EXISTS (SELECT 1 FROM pg_trigger
           WHERE tgrelid='public.stewardship_directory_export_snapshot'::regclass
             AND tgname='directory_export_capture' AND NOT tgisinternal
             AND tgfoid='public.stewardship_directory_export_capture_v1()'::regprocedure) THEN
        RAISE EXCEPTION 'The directory export capture through v2 was not installed';
    END IF;
    -- The action context admits the directory's new closed values, still
    -- refuses unknown ones, free text and wrong types, and still admits the
    -- earlier report and failure contexts (the body is whole, not just this
    -- change).
    IF NOT public.stewardship_safe_context_v1(
           'action', '{"outcome": "succeeded", "count": 2, "matching_count": 9,
                       "directory_response": "never-opened", "directory_data_check": "anything",
                       "directory_response_columns": true, "directory_sort": "submissions_desc",
                       "directory_reason": "any", "directory_phone": "any", "directory_reach": "any",
                       "search_used": false, "exact_code_used": false, "page": 1}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"directory_response": "yes", "directory_sort": "name"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"directory_sort": "duid_desc"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"directory_response": "not-submitted"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'action', '{"report_mode": "testing", "report_filter": "envelope",
                       "report_sort": "-submitted"}'::jsonb)
       OR NOT public.stewardship_safe_context_v1(
           'failure', '{"failure": "admin_command", "command": "export create"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"directory_response": "Smith"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"directory_data_check": "mailing"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"directory_response_columns": "yes"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"directory_sort": "-submitted"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'action', '{"directory_phone": "submitted"}'::jsonb)
       OR public.stewardship_safe_context_v1(
           'request', '{"directory_data_check": "any"}'::jsonb) THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 does not admit the directory filters as declared';
    END IF;
    IF (SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.proname='stewardship_safe_context_v1'
          AND NOT p.prosecdef AND p.provolatile='i'
          AND p.proconfig @> ARRAY['search_path=pg_catalog, public, pg_temp'])<>1 THEN
        RAISE EXCEPTION 'stewardship_safe_context_v1 is not installed as declared';
    END IF;
    IF current_setting('stewardship.migration_0043_acl', true) IS DISTINCT FROM (
           SELECT string_agg(p.proname||'='||coalesce(p.proacl::text,'default'),';' ORDER BY p.proname)
           FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
           WHERE n.nspname='public' AND p.proname IN
               ('stewardship_directory_export_capture_v1','stewardship_safe_context_v1')) THEN
        RAISE EXCEPTION 'A replaced function lost or changed its grants';
    END IF;
END
$check$;
