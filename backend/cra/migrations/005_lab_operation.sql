ALTER TABLE labs ADD COLUMN current_job_id uuid REFERENCES jobs(id);
UPDATE labs l SET current_job_id=(SELECT j.id FROM jobs j
 WHERE j.payload->>'lab_id'=l.id::text AND j.organization_id=l.organization_id
 AND j.actor_id=l.user_id ORDER BY j.created_at DESC,j.id DESC LIMIT 1);
