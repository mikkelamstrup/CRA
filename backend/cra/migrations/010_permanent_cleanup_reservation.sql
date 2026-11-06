ALTER TABLE node_orphans ADD COLUMN cleanup_reserved boolean NOT NULL DEFAULT false;
UPDATE node_orphans SET cleanup_reserved=true WHERE job_id IS NOT NULL OR status IN ('QUEUED','RESOLVED','FAILED');
CREATE OR REPLACE FUNCTION fence_lab_restoration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 1415230005));
    IF NEW.state <> 'DESTROYED' AND EXISTS (
        SELECT 1 FROM node_orphans WHERE lab_id=NEW.id AND cleanup_reserved
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='orphan_cleanup_reserved';
    END IF;
    RETURN NEW;
END;
$$;
CREATE FUNCTION preserve_cleanup_reservation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.cleanup_reserved AND NOT NEW.cleanup_reserved THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='cleanup_reservation_is_permanent';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER permanent_cleanup_reservation BEFORE UPDATE ON node_orphans
FOR EACH ROW EXECUTE FUNCTION preserve_cleanup_reservation();
