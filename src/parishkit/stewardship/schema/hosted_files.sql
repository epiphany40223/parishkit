-- Hosted files for parishioners (#346). One row per uploaded file: the bytes
-- live on disk under the media root, and this row is their receipt (type,
-- size, digest) and public identity (an unguessable token). Only the slug
-- can change, and neither a slug change nor a deletion is allowed while
-- current content uses the file. See docs/specs/stewardship/hosted-files.
CREATE TABLE public.stewardship_hosted_file (
    id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    actor_id uuid,
    correlation_id uuid NOT NULL,
    updated_at timestamp with time zone DEFAULT statement_timestamp() NOT NULL,
    version bigint NOT NULL,
    slug character varying(64) NOT NULL,
    original_name character varying(200) NOT NULL,
    kind character varying(8) NOT NULL,
    size integer NOT NULL,
    sha256 character varying(64) NOT NULL,
    width integer,
    height integer,
    token character varying(43) NOT NULL,
    uploaded_by_id uuid NOT NULL,
    CONSTRAINT hosted_file_digest CHECK (((sha256)::text ~ '^[0-9a-f]{64}$'::text)),
    CONSTRAINT hosted_file_dimensions CHECK ((((height >= 1) AND (height <= 2048) AND ((kind)::text = ANY ((ARRAY['png'::character varying, 'jpeg'::character varying])::text[])) AND (width >= 1) AND (width <= 2048)) OR ((height IS NULL) AND ((kind)::text = ANY ((ARRAY['pdf'::character varying, 'docx'::character varying, 'xlsx'::character varying, 'pptx'::character varying])::text[])) AND (width IS NULL)))),
    CONSTRAINT hosted_file_kind CHECK (((kind)::text = ANY ((ARRAY['pdf'::character varying, 'docx'::character varying, 'xlsx'::character varying, 'pptx'::character varying, 'png'::character varying, 'jpeg'::character varying])::text[]))),
    CONSTRAINT hosted_file_name CHECK (((original_name)::text ~ '^[^\x01-\x1f\x7f/\\]{1,200}$'::text)),
    CONSTRAINT hosted_file_size CHECK (((size >= 1) AND (size <= 10485760))),
    CONSTRAINT hosted_file_slug CHECK (((slug)::text ~ '^[a-z0-9]+(-[a-z0-9]+)*$'::text)),
    CONSTRAINT hosted_file_token CHECK (((token)::text ~ '^[A-Za-z0-9_-]{43}$'::text)),
    CONSTRAINT stewardship_accounts_hostedfile_positive_version CHECK ((version >= 1)),
    CONSTRAINT stewardship_hosted_file_height_check CHECK ((height >= 0)),
    CONSTRAINT stewardship_hosted_file_size_check CHECK ((size >= 0)),
    CONSTRAINT stewardship_hosted_file_version_check CHECK ((version >= 0)),
    CONSTRAINT stewardship_hosted_file_width_check CHECK ((width >= 0)),
    CONSTRAINT stewardship_hosted_file_pkey PRIMARY KEY (id),
    CONSTRAINT hosted_file_slug_unique UNIQUE (slug),
    CONSTRAINT hosted_file_token_unique UNIQUE (token)
);
CREATE INDEX stewardship_hosted_file_correlation_id_4eafb8f2 ON public.stewardship_hosted_file USING btree (correlation_id);

-- Every current use of each hosted file, one row per use (see "Current
-- content" in the spec): content of the active configuration for campaigns
-- that can still render (not archived, or archived but still the current
-- campaign), pending configuration changes, and email not yet sent. A
-- content use names a placeholder for the file's slug, or its public link.
-- The Admin page reads this view; the guard below refuses a slug change or a
-- deletion while any row exists for the file.
CREATE VIEW public.stewardship_hosted_file_use AS
 WITH runtime AS (
         SELECT s.active_configuration_id, s.current_campaign_id
           FROM public.stewardship_system_configuration s
        ), patterns AS (
         SELECT f.id, ('/files/'::text || (f.token)::text) AS link,
            (('\{\{ *file\.'::text || (f.slug)::text) || ' *\}\}'::text) AS pattern
           FROM public.stewardship_hosted_file f
        )
 SELECT p.id AS file_id, 'content'::text AS source, c.campaign_id,
    (c.kind)::text AS kind, (c.slot)::text AS slot, (1)::bigint AS count
   FROM (((patterns p
     JOIN public.stewardship_content_version c ON (((c.html ~ p.pattern) OR (c.text ~ p.pattern) OR (strpos(c.html, p.link) > 0) OR (strpos(c.text, p.link) > 0))))
     JOIN runtime r ON ((c.configuration_id = r.active_configuration_id)))
     LEFT JOIN public.stewardship_campaign k ON ((k.id = c.campaign_id)))
  WHERE ((k.id IS NULL) OR ((k.state)::text = ANY ((ARRAY['draft'::character varying, 'scheduled'::character varying, 'active'::character varying, 'closed'::character varying])::text[])) OR (k.id = r.current_campaign_id))
UNION ALL
 SELECT p.id AS file_id, 'pending_change'::text AS source, NULL::uuid AS campaign_id,
    NULL::text AS kind, NULL::text AS slot, count(*) AS count
   FROM (patterns p
     JOIN public.stewardship_config_request q ON ((((q.patch)::text ~ p.pattern) OR (strpos((q.patch)::text, p.link) > 0))))
  WHERE (COALESCE(( SELECT (c.state)::text AS state
           FROM public.stewardship_config_checkpoint c
          WHERE (c.request_id = q.id)
          ORDER BY c.sequence DESC
         LIMIT 1), 'pending'::text) <> ALL (ARRAY['applied'::text, 'failed'::text, 'cancelled'::text]))
  GROUP BY p.id
UNION ALL
 SELECT p.id AS file_id, 'unsent_mail'::text AS source, NULL::uuid AS campaign_id,
    NULL::text AS kind, NULL::text AS slot, count(*) AS count
   FROM ((patterns p
     JOIN public.stewardship_outbox_render o ON (((strpos(o.html, p.link) > 0) OR (strpos(o.text, p.link) > 0))))
     JOIN public.stewardship_outbox_message m ON ((m.render_id = o.id)))
  WHERE ((m.state)::text = ANY ((ARRAY['pending'::character varying, 'submitting'::character varying, 'retry_wait'::character varying, 'delivery_unknown'::character varying])::text[]))
  GROUP BY p.id
UNION ALL
 SELECT p.id AS file_id, 'unsent_mail'::text AS source, NULL::uuid AS campaign_id,
    NULL::text AS kind, NULL::text AS slot, count(*) AS count
   FROM (patterns p
     JOIN public.stewardship_campaign_mail_test t ON ((strpos((t.mail)::text, p.link) > 0)))
  WHERE ((t.state)::text = ANY ((ARRAY['queued'::character varying, 'submitting'::character varying, 'delivery_unknown'::character varying])::text[]))
  GROUP BY p.id;

-- FUNCTION: stewardship_hosted_file_mutable_v1()
CREATE FUNCTION public.stewardship_hosted_file_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."original_name" IS DISTINCT FROM OLD."original_name" OR NEW."kind" IS DISTINCT FROM OLD."kind" OR NEW."size" IS DISTINCT FROM OLD."size" OR NEW."sha256" IS DISTINCT FROM OLD."sha256" OR NEW."width" IS DISTINCT FROM OLD."width" OR NEW."height" IS DISTINCT FROM OLD."height" OR NEW."token" IS DISTINCT FROM OLD."token" OR NEW."uploaded_by_id" IS DISTINCT FROM OLD."uploaded_by_id" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.version IS DISTINCT FROM OLD.version + 1 THEN
                    RAISE EXCEPTION 'Every update must advance the record version'
                        USING ERRCODE = '23514';
                END IF;
                NEW.updated_at := statement_timestamp();
                RETURN NEW;
            END;
            $$;

-- FUNCTION: stewardship_hosted_file_guard_v1()
-- SECURITY DEFINER (never callable directly) so the in-use check reads
-- content, pending changes and unsent mail the web role cannot read itself.
-- Inserts serialize on one transaction lock, so two uploads cannot both pass
-- the library caps (100 files, 200 MB).
CREATE FUNCTION public.stewardship_hosted_file_guard_v1() RETURNS trigger
    LANGUAGE plpgsql SECURITY DEFINER
    SET search_path TO 'pg_catalog', 'public', 'pg_temp'
    AS $$
DECLARE files bigint; total bigint;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.version <> 1 THEN
            RAISE EXCEPTION 'A hosted file starts at version 1' USING ERRCODE='23514';
        END IF;
        PERFORM pg_advisory_xact_lock(736346, 1);
        SELECT count(*), coalesce(sum(size), 0) INTO files, total
        FROM public.stewardship_hosted_file;
        IF files >= 100 OR total + NEW.size > 209715200 THEN
            RAISE EXCEPTION 'The hosted file library is full' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.slug IS NOT DISTINCT FROM OLD.slug THEN
        RETURN NEW;
    END IF;
    IF EXISTS (SELECT 1 FROM public.stewardship_hosted_file_use WHERE file_id = OLD.id) THEN
        RAISE EXCEPTION 'Hosted file is in use' USING ERRCODE='23514';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_hosted_file_guard_v1() FROM PUBLIC;

CREATE TRIGGER stewardship_hosted_file_guard_v1 BEFORE INSERT OR DELETE OR UPDATE ON public.stewardship_hosted_file FOR EACH ROW EXECUTE FUNCTION public.stewardship_hosted_file_guard_v1();
CREATE TRIGGER stewardship_hosted_file_mutable_guard_v1 BEFORE UPDATE ON public.stewardship_hosted_file FOR EACH ROW EXECUTE FUNCTION public.stewardship_hosted_file_mutable_v1();
