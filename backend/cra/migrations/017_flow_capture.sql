CREATE TABLE lab_capture_snapshots (
    lab_id uuid PRIMARY KEY REFERENCES labs(id) ON DELETE CASCADE,
    generation integer NOT NULL CHECK(generation > 0),
    fence_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    captures jsonb NOT NULL
);
CREATE TABLE flow_history (
    event_id uuid PRIMARY KEY,
    sequence bigserial UNIQUE NOT NULL,
    schema_version integer NOT NULL CHECK(schema_version = 1),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    lab_id uuid NOT NULL REFERENCES labs(id) ON DELETE CASCADE,
    generation integer NOT NULL CHECK(generation > 0),
    operator_user_id uuid NOT NULL REFERENCES users(id),
    epoch uuid NOT NULL,
    window_sequence bigint NOT NULL,
    observed_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload jsonb NOT NULL,
    UNIQUE(lab_id,generation,operator_user_id,epoch,window_sequence)
);
CREATE INDEX flow_history_lab_sequence ON flow_history(lab_id,sequence DESC);
CREATE INDEX flow_history_org_sequence ON flow_history(organization_id,sequence DESC);
CREATE INDEX flow_history_received ON flow_history(received_at);
