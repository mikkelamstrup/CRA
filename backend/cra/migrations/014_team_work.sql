ALTER TABLE labs DROP CONSTRAINT labs_slot_check;
ALTER TABLE labs ADD CONSTRAINT labs_slot_check CHECK(slot BETWEEN 0 AND 5);
ALTER TABLE node_orphans DROP CONSTRAINT node_orphans_slot_check;
ALTER TABLE node_orphans ADD CONSTRAINT node_orphans_slot_check CHECK(slot BETWEEN 0 AND 5);
CREATE TABLE team_notebooks (
    team_id uuid PRIMARY KEY REFERENCES teams(id), body text NOT NULL CHECK(length(body)<=16000),
    revision integer NOT NULL CHECK(revision>0), updated_by uuid NOT NULL REFERENCES users(id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE team_findings (
    id uuid PRIMARY KEY, team_id uuid NOT NULL REFERENCES teams(id),
    actor_id uuid NOT NULL REFERENCES users(id), request_key uuid NOT NULL,
    body text NOT NULL CHECK(length(body) BETWEEN 1 AND 2000),
    source text NOT NULL CHECK(length(source) BETWEEN 1 AND 300),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(team_id,actor_id,request_key)
);
ALTER TABLE teams ADD COLUMN creation_members jsonb;
UPDATE teams t SET creation_members=coalesce((SELECT jsonb_agg(jsonb_build_object('user_id',m.user_id::text,'role',m.role) ORDER BY m.ordinal) FROM team_members m WHERE m.team_id=t.id),'[]'::jsonb);
ALTER TABLE teams ALTER COLUMN creation_members SET NOT NULL;
