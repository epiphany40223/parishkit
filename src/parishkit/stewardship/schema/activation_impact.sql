-- Constant-size evidence for the enumerated mail-impact preview. Never grant
-- runtime writers UPDATE/DELETE: only private statement triggers advance it.
CREATE TABLE public.stewardship_activation_impact (
    singleton boolean NOT NULL PRIMARY KEY,
    version bigint DEFAULT 1 NOT NULL CHECK(version>=0),
    CONSTRAINT activation_impact_singleton CHECK(singleton),
    CONSTRAINT activation_impact_positive CHECK(version>=1)
);

CREATE FUNCTION public.stewardship_activation_impact_tick_v1() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO pg_catalog,public,pg_temp AS $$
BEGIN
    INSERT INTO public.stewardship_activation_impact(singleton,version) VALUES(true,1)
    ON CONFLICT(singleton) DO UPDATE SET version=stewardship_activation_impact.version+1;
    RETURN NULL;
END $$;
REVOKE ALL ON FUNCTION public.stewardship_activation_impact_tick_v1() FROM PUBLIC;

-- One tick per statement, not per Family. Columns match the enumerating readers
-- in readiness_families/readiness_digests/reports.readiness_weekly. Updates may
-- invalidate conservatively even when a value is unchanged; activity is omitted.
CREATE TRIGGER activation_impact AFTER INSERT OR DELETE OR UPDATE OF
    active,email_eligible,email_deliverable,effective_submission_id,source_generation
    ON public.stewardship_family_campaign FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_schedule_definition FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_schedule_revision FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_schedule_occurrence FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_schedule_fulfillment FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_restore_delivery_hold FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_weekly_digest_preparation FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_weekly_digest_snapshot FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_weekly_digest_recipient FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_weekly_manual_request FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR DELETE OR UPDATE OF disposition
    ON public.stewardship_additional_information FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR UPDATE OR DELETE
    ON public.stewardship_submission FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
CREATE TRIGGER activation_impact AFTER INSERT OR DELETE OR UPDATE OF state
    ON public.stewardship_outbox_message FOR EACH STATEMENT
    EXECUTE FUNCTION public.stewardship_activation_impact_tick_v1();
