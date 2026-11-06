CREATE TABLE lab_flags (
    lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
    objective_id text NOT NULL, verifier text NOT NULL CHECK(verifier ~ '^[0-9a-f]{64}$'),
    PRIMARY KEY(lab_id,generation,objective_id)
);
CREATE TABLE lab_completions (
    lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
    objective_id text NOT NULL, completed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(lab_id,generation,objective_id)
);
CREATE TABLE score_ledger (
    id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id), mission_id text NOT NULL, mission_version integer NOT NULL,
    objective_id text NOT NULL, lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
    points integer NOT NULL CHECK(points BETWEEN 0 AND 1000), created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(organization_id,user_id,mission_id,mission_version,objective_id)
);
CREATE TABLE objective_attempts (
    id uuid PRIMARY KEY, lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
    objective_id text NOT NULL, user_id uuid NOT NULL REFERENCES users(id), request_key uuid NOT NULL,
    fingerprint text NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(lab_id,user_id,request_key)
);
CREATE INDEX objective_attempts_limit ON objective_attempts(user_id,created_at);
