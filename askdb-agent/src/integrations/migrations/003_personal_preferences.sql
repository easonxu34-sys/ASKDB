ALTER TABLE model_profiles ADD COLUMN model_kind TEXT NOT NULL DEFAULT 'chat' CHECK (model_kind IN ('chat','embedding','rerank'));
ALTER TABLE model_profiles ADD COLUMN service_options JSONB NOT NULL DEFAULT '{}';
CREATE TABLE model_service_settings (kind TEXT PRIMARY KEY CHECK (kind IN ('embedding','rerank','memory_chat')), profile_id TEXT NOT NULL REFERENCES model_profiles(id));
CREATE TABLE model_profile_versions (profile_id TEXT NOT NULL, revision TEXT NOT NULL, configuration JSONB NOT NULL, api_key_ciphertext BYTEA, PRIMARY KEY(profile_id,revision));
CREATE TABLE personal_memory_settings (owner TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0, revision BIGINT NOT NULL DEFAULT 0, write_epoch BIGINT NOT NULL DEFAULT 0);
CREATE TABLE personal_memories (
 id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('expression','display','analysis_steps','default_filter','metric_definition','analysis_recipe')),
 source_id TEXT, scenario TEXT NOT NULL, content TEXT NOT NULL, payload JSONB NOT NULL DEFAULT '{}', version BIGINT NOT NULL DEFAULT 1,
 status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','superseded','deleted')), expires_at TIMESTAMPTZ, content_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX personal_memory_owner_scope ON personal_memories(owner,source_id,status);
CREATE TABLE personal_memory_operations (
 owner TEXT NOT NULL, id TEXT NOT NULL, kind TEXT NOT NULL, payload JSONB NOT NULL, expires_at TIMESTAMPTZ, consumed INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(owner,id)
);
CREATE TABLE personal_memory_generations (
 id TEXT PRIMARY KEY, profile_id TEXT NOT NULL, profile_revision TEXT NOT NULL, dimensions INTEGER NOT NULL, space_hash TEXT NOT NULL,
 state TEXT NOT NULL CHECK (state IN ('building','active','retired')), created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX personal_memory_active_generation ON personal_memory_generations(state) WHERE state='active';
CREATE TABLE personal_memory_embeddings (
 memory_id TEXT NOT NULL REFERENCES personal_memories(id), version BIGINT NOT NULL, generation TEXT NOT NULL REFERENCES personal_memory_generations(id),
 content_hash TEXT NOT NULL, embedding public.vector NOT NULL, PRIMARY KEY(memory_id,generation)
);
CREATE TABLE personal_memory_index_jobs (
 memory_id TEXT NOT NULL REFERENCES personal_memories(id), version BIGINT NOT NULL, content_hash TEXT NOT NULL, generation TEXT NOT NULL REFERENCES personal_memory_generations(id),
 attempts INTEGER NOT NULL DEFAULT 0, retry_at TIMESTAMPTZ NOT NULL DEFAULT now(), error_code TEXT, PRIMARY KEY(memory_id,generation)
);
