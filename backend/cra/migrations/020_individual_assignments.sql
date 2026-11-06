ALTER TABLE memberships ADD COLUMN teacher_alias text NOT NULL DEFAULT '' CHECK(length(teacher_alias)<=80);
ALTER TABLE memberships ADD COLUMN alias_revision integer NOT NULL DEFAULT 0 CHECK(alias_revision>=0);
CREATE TABLE mission_releases (
    organization_id uuid NOT NULL,
    user_id uuid NOT NULL,
    mission_id text NOT NULL CHECK(length(mission_id) BETWEEN 1 AND 48),
    mission_version integer NOT NULL CHECK(mission_version>0),
    released_by uuid NOT NULL REFERENCES users(id),
    released_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(organization_id,user_id,mission_id,mission_version),
    FOREIGN KEY(user_id,organization_id) REFERENCES memberships(user_id,organization_id)
);
