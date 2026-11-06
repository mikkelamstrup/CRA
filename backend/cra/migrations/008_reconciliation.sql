ALTER TABLE jobs DROP CONSTRAINT jobs_kind_check;
ALTER TABLE jobs ADD CONSTRAINT jobs_kind_check CHECK(kind IN ('create','start','stop','reset','destroy','test-probe','orphan-destroy'));
CREATE TABLE node_orphans (
    lab_id uuid PRIMARY KEY,
    organization_id uuid NOT NULL,
    user_id uuid NOT NULL,
    generation integer NOT NULL,
    slot integer NOT NULL CHECK(slot BETWEEN 0 AND 1),
    first_seen_at timestamptz NOT NULL DEFAULT now(),
    last_seen_at timestamptz NOT NULL DEFAULT now(),
    observations integer NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'OBSERVED' CHECK(status IN ('OBSERVED','QUEUED','RESOLVED','QUARANTINED','FAILED')),
    job_id uuid REFERENCES jobs(id),
    error_code text
);
