ALTER TABLE outbox ADD COLUMN event_occurred_at timestamptz;
UPDATE outbox SET event_occurred_at=(event->>'occurred_at')::timestamptz;
ALTER TABLE outbox ALTER COLUMN event_occurred_at SET NOT NULL;
ALTER TABLE outbox ADD COLUMN recorded_at timestamptz;
-- Older rows have no separately measured receipt timestamp; keep NULL.
ALTER TABLE outbox ALTER COLUMN recorded_at SET DEFAULT clock_timestamp();
CREATE INDEX outbox_lab_generation_time ON outbox(organization_id,(event->>'lab_id'),(event->>'generation'),event_occurred_at DESC);
CREATE INDEX flow_history_replay ON flow_history(organization_id,lab_id,generation,observed_at DESC);
