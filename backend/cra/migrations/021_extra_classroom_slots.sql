ALTER TABLE labs DROP CONSTRAINT labs_slot_check;
ALTER TABLE labs ADD CONSTRAINT labs_slot_check CHECK(slot BETWEEN 0 AND 17);
ALTER TABLE node_orphans DROP CONSTRAINT node_orphans_slot_check;
ALTER TABLE node_orphans ADD CONSTRAINT node_orphans_slot_check CHECK(slot BETWEEN 0 AND 17);
