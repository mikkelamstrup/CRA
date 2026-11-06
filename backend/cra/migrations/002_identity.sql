CREATE TABLE organizations (id uuid PRIMARY KEY, name text NOT NULL);
CREATE TABLE users (
    id uuid PRIMARY KEY,
    username text UNIQUE NOT NULL CHECK (username ~ '^[a-z0-9][a-z0-9_-]{2,47}$'),
    password_hash text NOT NULL,
    active boolean NOT NULL DEFAULT true
);
CREATE TABLE memberships (
    user_id uuid REFERENCES users(id), organization_id uuid REFERENCES organizations(id),
    role text NOT NULL CHECK (role IN ('student','teacher')),
    PRIMARY KEY(user_id, organization_id)
);
CREATE TABLE auth_sessions (
    token_hash text PRIMARY KEY,
    user_id uuid NOT NULL REFERENCES users(id),
    organization_id uuid NOT NULL REFERENCES organizations(id),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY(user_id, organization_id) REFERENCES memberships(user_id, organization_id)
);
CREATE INDEX auth_sessions_user ON auth_sessions(user_id);
CREATE TABLE login_limits (
    key text PRIMARY KEY, window_start timestamptz NOT NULL, attempts integer NOT NULL
);
