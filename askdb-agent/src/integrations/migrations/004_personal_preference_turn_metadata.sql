ALTER TABLE agent_turn_requests ADD COLUMN analysis_descriptor JSONB;
ALTER TABLE agent_turn_requests ADD COLUMN personal_events JSONB NOT NULL DEFAULT '[]';
