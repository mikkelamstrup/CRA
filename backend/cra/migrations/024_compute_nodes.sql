-- Existing resources stay bound to node 1, without rewriting jobs or VM state.
ALTER TABLE labs ADD COLUMN node_id integer NOT NULL DEFAULT 1 CHECK(node_id IN (1,2));
ALTER TABLE labs DROP CONSTRAINT labs_slot_key;
ALTER TABLE labs ADD CONSTRAINT labs_node_slot_key UNIQUE(node_id,slot);
CREATE FUNCTION immutable_compute_node() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.node_id <> OLD.node_id THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='compute_binding_is_permanent';
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER labs_compute_binding BEFORE UPDATE ON labs
FOR EACH ROW EXECUTE FUNCTION immutable_compute_node();
ALTER TABLE node_orphans ADD COLUMN node_id integer NOT NULL DEFAULT 1 CHECK(node_id IN (1,2));
ALTER TABLE node_orphans DROP CONSTRAINT node_orphans_pkey;
ALTER TABLE node_orphans ADD PRIMARY KEY(node_id,lab_id);
CREATE TRIGGER orphan_compute_binding BEFORE UPDATE ON node_orphans
FOR EACH ROW EXECUTE FUNCTION immutable_compute_node();
ALTER TABLE node_status DROP CONSTRAINT node_status_id_check;
ALTER TABLE node_status ADD CONSTRAINT node_status_id_check CHECK(id IN (1,2));
ALTER TABLE node_status ADD COLUMN physical_memory_mib integer CHECK(physical_memory_mib>=0);
ALTER TABLE node_status ADD COLUMN running_declared_memory_mib integer CHECK(running_declared_memory_mib>=0);
