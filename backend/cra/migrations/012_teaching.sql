CREATE TABLE teaching_sessions (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 created_by uuid NOT NULL REFERENCES users(id), request_key uuid NOT NULL,
 initial_day integer NOT NULL CHECK(initial_day BETWEEN 1 AND 8),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 100), day integer NOT NULL CHECK(day BETWEEN 1 AND 8),
 phase text NOT NULL DEFAULT 'theory' CHECK(phase IN ('theory','solo','team','debrief')),
 terminal_enabled boolean NOT NULL DEFAULT true, announcement text NOT NULL DEFAULT '' CHECK(length(announcement)<=2000),
 closed boolean NOT NULL DEFAULT false, revision integer NOT NULL DEFAULT 1,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,created_by,request_key)
);
CREATE UNIQUE INDEX one_open_teaching_session ON teaching_sessions(organization_id) WHERE NOT closed;
CREATE TABLE help_requests (
 id uuid PRIMARY KEY, organization_id uuid NOT NULL REFERENCES organizations(id),
 user_id uuid NOT NULL REFERENCES users(id), session_id uuid REFERENCES teaching_sessions(id),
 request_key uuid NOT NULL, message text NOT NULL CHECK(length(message) BETWEEN 1 AND 2000),
 status text NOT NULL DEFAULT 'open' CHECK(status IN ('open','answered','resolved')),
 reply text NOT NULL DEFAULT '' CHECK(length(reply)<=2000), responder_id uuid REFERENCES users(id),
 revision integer NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(organization_id,user_id,request_key)
);
CREATE UNIQUE INDEX one_open_help_request ON help_requests(organization_id,user_id) WHERE status IN ('open','answered');
CREATE TABLE teaching_events (
 sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, event_id uuid NOT NULL UNIQUE,
 organization_id uuid NOT NULL REFERENCES organizations(id), session_id uuid REFERENCES teaching_sessions(id),
 actor_id uuid NOT NULL REFERENCES users(id), kind text NOT NULL,
 schema_version integer NOT NULL DEFAULT 1 CHECK(schema_version=1),
 source text NOT NULL DEFAULT 'teaching' CHECK(source='teaching'),
 evidence_level text NOT NULL DEFAULT 'system' CHECK(evidence_level IN ('system','submitted')),
 occurred_at timestamptz NOT NULL DEFAULT now(), payload jsonb NOT NULL
);
