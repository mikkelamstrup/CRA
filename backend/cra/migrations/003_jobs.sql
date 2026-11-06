CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id),
    actor_id uuid NOT NULL REFERENCES users(id),
    request_key text NOT NULL CHECK(length(request_key) BETWEEN 1 AND 100),
    kind text NOT NULL CHECK(kind IN ('create','start','stop','reset','destroy','test-probe')),
    payload jsonb NOT NULL,
    status text NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED')),
    attempts integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL DEFAULT 5 CHECK(max_attempts BETWEEN 1 AND 10),
    lease_token uuid,
    lease_owner text,
    lease_until timestamptz,
    available_at timestamptz NOT NULL DEFAULT now(),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(organization_id, actor_id, request_key)
);
CREATE INDEX jobs_available ON jobs(status, available_at, lease_until);
CREATE TABLE outbox (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id uuid NOT NULL UNIQUE,
    organization_id uuid NOT NULL REFERENCES organizations(id),
    event jsonb NOT NULL,
    lease_token uuid,
    lease_until timestamptz,
    delivered_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE event_receipts (
    consumer text NOT NULL, event_id uuid NOT NULL REFERENCES outbox(event_id),
    received_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(consumer, event_id)
);
