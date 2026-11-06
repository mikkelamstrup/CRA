ALTER TABLE labs ADD COLUMN desktop_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE terminal_grants ADD COLUMN purpose text NOT NULL DEFAULT 'terminal' CHECK(purpose IN ('terminal','desktop'));
