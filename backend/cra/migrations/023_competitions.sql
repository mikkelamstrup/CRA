CREATE TABLE competitions (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 created_by uuid NOT NULL REFERENCES users(id), request_key uuid NOT NULL,
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 80),
 status text NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','running','paused','ended')),
 duration_seconds integer NOT NULL CHECK(duration_seconds BETWEEN 60 AND 28800),
 remaining_seconds double precision NOT NULL, revision integer NOT NULL DEFAULT 1,
 started_at timestamptz, deadline timestamptz, frozen_at timestamptz,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,organization_id), UNIQUE(organization_id,request_key)
);
CREATE UNIQUE INDEX one_open_competition ON competitions(organization_id) WHERE status<>'ended';
CREATE TABLE competition_teams (
 competition_id uuid NOT NULL, organization_id uuid NOT NULL, team_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal BETWEEN 1 AND 20),
 PRIMARY KEY(competition_id,team_id), UNIQUE(competition_id,ordinal),
 FOREIGN KEY(competition_id,organization_id) REFERENCES competitions(id,organization_id),
 FOREIGN KEY(team_id,organization_id) REFERENCES teams(id,organization_id)
);
CREATE TABLE competition_scores (
 id uuid PRIMARY KEY, competition_id uuid NOT NULL, team_id uuid NOT NULL,
 challenge_id text NOT NULL, lab_id uuid NOT NULL REFERENCES labs(id), generation integer NOT NULL,
 actor_id uuid NOT NULL REFERENCES users(id), points integer NOT NULL CHECK(points BETWEEN 1 AND 1000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(competition_id,team_id,challenge_id),
 FOREIGN KEY(competition_id,team_id) REFERENCES competition_teams(competition_id,team_id)
);
CREATE FUNCTION protect_competition_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'competition score history is immutable'; END; $$;
CREATE TRIGGER immutable_competition_scores BEFORE UPDATE OR DELETE ON competition_scores
 FOR EACH ROW EXECUTE FUNCTION protect_competition_history();
CREATE TABLE competition_attempts (
 id uuid PRIMARY KEY, competition_id uuid NOT NULL REFERENCES competitions(id),
 actor_id uuid NOT NULL REFERENCES users(id), request_key uuid NOT NULL, fingerprint text NOT NULL,
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(competition_id,actor_id,request_key)
);
CREATE INDEX competition_attempt_limit ON competition_attempts(actor_id,created_at);
