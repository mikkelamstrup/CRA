CREATE TABLE labs (
    id uuid PRIMARY KEY,
    organization_id uuid NOT NULL REFERENCES organizations(id),
    user_id uuid NOT NULL REFERENCES users(id),
    mission_id text NOT NULL,
    mission_version integer NOT NULL,
    generation integer NOT NULL DEFAULT 1,
    state text NOT NULL,
    desired_state text NOT NULL DEFAULT 'READY',
    slot integer UNIQUE CHECK(slot BETWEEN 0 AND 1),
    target_ip inet,
    last_observed_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id,organization_id) REFERENCES memberships(user_id,organization_id)
);
CREATE UNIQUE INDEX one_active_lab_per_user ON labs(organization_id,user_id) WHERE state<>'DESTROYED';
