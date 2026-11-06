CREATE TABLE notebooks (
 organization_id uuid NOT NULL REFERENCES organizations(id),
 user_id uuid NOT NULL REFERENCES users(id),
 day integer NOT NULL CHECK(day BETWEEN 1 AND 8),
 body text NOT NULL DEFAULT '' CHECK(length(body)<=16000),
 reflection text NOT NULL DEFAULT '' CHECK(length(reflection)<=4000),
 theory_read boolean NOT NULL DEFAULT false,
 revision integer NOT NULL DEFAULT 1 CHECK(revision>0),
 updated_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(organization_id,user_id,day)
);
