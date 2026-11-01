-- The body the fresh-install baseline held before frozen migration 0200
-- (#2343) replaced it, copied byte for byte from
-- src/parishkit/stewardship/schema/functions.sql at 94abae92b, the main commit
-- 0200 was written on. The verified-address match tests install it to prove
-- 0200's DO block refuses the earlier update guard.

CREATE OR REPLACE FUNCTION public.stewardship_portal_user_mutable_v1() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
            BEGIN
                IF NEW."id" IS DISTINCT FROM OLD."id" OR NEW."created_at" IS DISTINCT FROM OLD."created_at" OR NEW."google_subject" IS DISTINCT FROM OLD."google_subject" THEN
                    RAISE EXCEPTION 'Record identity and bindings are immutable'
                        USING ERRCODE = '23514';
                END IF;
                -- #306 M2: disabling is one-way. No path re-enables an
                -- account; a future Administrator path needs its own evidence.
                IF OLD."disabled" AND NOT NEW."disabled" THEN
                    RAISE EXCEPTION 'A disabled portal user cannot be re-enabled'
                        USING ERRCODE = '23514';
                END IF;
                -- #306 M2: the email and hosted-domain claims change only with
                -- a re-verification stamped in this same transaction, as the
                -- Google sign-in callback does; verified_at only advances.
                IF (NEW."email", NEW."hosted_domain", NEW."verified_at")
                   IS DISTINCT FROM (OLD."email", OLD."hosted_domain", OLD."verified_at")
                   AND (NEW."verified_at" <= OLD."verified_at"
                        OR NEW."verified_at" < transaction_timestamp()
                        OR NEW."verified_at" > statement_timestamp()) THEN
                    RAISE EXCEPTION 'Identity claims change only with a fresh verification'
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
