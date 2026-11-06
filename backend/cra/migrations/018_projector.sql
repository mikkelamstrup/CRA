CREATE TABLE projector_grants (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 issuer_id uuid NOT NULL REFERENCES users(id),
 exchange_hash text UNIQUE NOT NULL, exchange_until timestamptz NOT NULL,
 session_hash text UNIQUE, exchanged_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL, revoked_at timestamptz
);
CREATE INDEX projector_grants_org ON projector_grants(organization_id,created_at);
CREATE TABLE projector_labs (
 grant_id uuid NOT NULL REFERENCES projector_grants(id) ON DELETE CASCADE,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 20),
 lab_id uuid REFERENCES labs(id) ON DELETE SET NULL,
 PRIMARY KEY(grant_id,ordinal), UNIQUE(grant_id,lab_id)
);
