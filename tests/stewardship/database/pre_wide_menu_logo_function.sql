CREATE OR REPLACE FUNCTION public.stewardship_branding_asset_insert_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
DECLARE selected public.stewardship_branding_bundle%ROWTYPE;
        maximum integer;
BEGIN
    PERFORM pg_advisory_xact_lock(736230,1);
    SELECT * INTO selected FROM public.stewardship_branding_bundle
        WHERE id=NEW.bundle_id;
    maximum := CASE NEW.label
        WHEN 'large' THEN 1024 WHEN 'banner' THEN 1024 WHEN 'section' THEN 256
        WHEN 'favicon' THEN 32 ELSE 128 END;
    IF NOT FOUND OR selected.state<>'writing'
       OR selected.expires_at<=clock_timestamp()
       OR NEW.actor_id IS DISTINCT FROM selected.owner_id
       OR NEW.width>maximum OR NEW.height>maximum THEN
        RAISE EXCEPTION 'Invalid branding asset receipt' USING ERRCODE='23514';
    END IF;
    NEW.created_at := clock_timestamp();
    RETURN NEW;
END;
$$;
