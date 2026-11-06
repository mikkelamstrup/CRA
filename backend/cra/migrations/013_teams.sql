CREATE TABLE teams (
    id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
    created_by uuid NOT NULL, name text NOT NULL CHECK(length(name) BETWEEN 1 AND 80),
    request_key uuid NOT NULL, revision integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(id,organization_id), UNIQUE(organization_id,created_by,request_key),
    FOREIGN KEY(created_by,organization_id) REFERENCES memberships(user_id,organization_id)
);
CREATE TABLE team_members (
    team_id uuid NOT NULL, organization_id uuid NOT NULL, user_id uuid NOT NULL,
    ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 3),
    role text NOT NULL CHECK(role IN ('coordinator','analyst','operator','reporter')),
    PRIMARY KEY(team_id,user_id), UNIQUE(team_id,ordinal),
    FOREIGN KEY(team_id,organization_id) REFERENCES teams(id,organization_id),
    FOREIGN KEY(user_id,organization_id) REFERENCES memberships(user_id,organization_id)
);
ALTER TABLE labs ADD COLUMN team_id uuid;
ALTER TABLE labs ADD CONSTRAINT lab_team_organization FOREIGN KEY(team_id,organization_id) REFERENCES teams(id,organization_id);
DROP INDEX one_active_lab_per_user;
CREATE UNIQUE INDEX one_active_lab_per_user ON labs(organization_id,user_id) WHERE state<>'DESTROYED' AND team_id IS NULL;
CREATE UNIQUE INDEX one_active_lab_per_team ON labs(team_id) WHERE state<>'DESTROYED' AND team_id IS NOT NULL;
CREATE TABLE lab_participants (
    lab_id uuid NOT NULL REFERENCES labs(id), user_id uuid NOT NULL REFERENCES users(id),
    ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 3),
    PRIMARY KEY(lab_id,user_id), UNIQUE(lab_id,ordinal)
);
CREATE TABLE team_score_ledger (
    id uuid PRIMARY KEY, team_id uuid NOT NULL REFERENCES teams(id),
    actor_id uuid NOT NULL REFERENCES users(id), mission_id text NOT NULL, mission_version integer NOT NULL,
    objective_id text NOT NULL, lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
    points integer NOT NULL CHECK(points BETWEEN 0 AND 1000), created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(team_id,mission_id,mission_version,objective_id)
);
