-- A durable cleanup reservation must also fence restoration after worker preflight.
-- Both enqueue/execute and this trigger use the same per-lab advisory lock.
CREATE FUNCTION fence_lab_restoration() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.id::text, 1415230005));
    IF NEW.state <> 'DESTROYED' AND EXISTS (
        SELECT 1 FROM node_orphans WHERE lab_id=NEW.id AND status IN ('QUEUED','RESOLVED','FAILED')
    ) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='orphan_cleanup_reserved';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER lab_restoration_fence BEFORE INSERT OR UPDATE ON labs
FOR EACH ROW EXECUTE FUNCTION fence_lab_restoration();
