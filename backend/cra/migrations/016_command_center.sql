ALTER TABLE jobs ADD COLUMN resource_owner_id uuid REFERENCES users(id);
UPDATE jobs SET resource_owner_id=actor_id;
ALTER TABLE jobs ALTER COLUMN resource_owner_id SET NOT NULL;
CREATE INDEX jobs_resource_history ON jobs(organization_id,resource_owner_id);
CREATE INDEX outbox_organization_cursor ON outbox(organization_id,sequence);
CREATE INDEX teaching_organization_cursor ON teaching_events(organization_id,sequence);
CREATE TABLE node_status (
 id integer PRIMARY KEY CHECK(id=1), connected boolean NOT NULL,
 available_memory_mib integer NOT NULL CHECK(available_memory_mib>=0),
 free_disk_gib integer NOT NULL CHECK(free_disk_gib>=0),
 active_labs integer NOT NULL CHECK(active_labs>=0),
 reservations integer NOT NULL CHECK(reservations>=0),
 observed_at timestamptz NOT NULL, received_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE teaching_events ADD COLUMN received_at timestamptz;
UPDATE teaching_events SET received_at=occurred_at;
ALTER TABLE teaching_events ALTER COLUMN received_at SET DEFAULT clock_timestamp();
ALTER TABLE teaching_events ALTER COLUMN received_at SET NOT NULL;
