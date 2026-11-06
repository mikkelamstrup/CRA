CREATE TABLE terminal_grants (
    token_hash text PRIMARY KEY,
    session_hash text NOT NULL REFERENCES auth_sessions(token_hash) ON DELETE CASCADE,
    lab_id uuid NOT NULL REFERENCES labs(id),
    organization_id uuid NOT NULL,
    user_id uuid NOT NULL,
    generation integer NOT NULL,
    expires_at timestamptz NOT NULL,
    consumed_at timestamptz,
    closed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX terminal_grants_lab ON terminal_grants(lab_id,generation);
