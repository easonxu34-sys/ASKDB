CREATE TABLE agent_memory_journal_state (
  id INT PRIMARY KEY CHECK (id = 1),
  journal_applied_seq INT NOT NULL DEFAULT 0,
  healthy INT NOT NULL DEFAULT 1,
  last_error_code TEXT,
  updated_at TEXT NOT NULL,
  journal_initialized INT NOT NULL DEFAULT 0,
  journal_id TEXT
);

CREATE TABLE agent_memory_suppressions (
  event_sequence INT NOT NULL,
  data_source_id TEXT NOT NULL,
  item_type TEXT NOT NULL,
  item_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (event_sequence, item_type, item_id)
);

CREATE TABLE agent_thread_creation_requests (
  owner_user_id TEXT NOT NULL,
  creation_key_hash TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  thread_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (owner_user_id, creation_key_hash)
);

CREATE TABLE agent_thread_deletion_impacts (
  impact_version TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  data_source_id TEXT NOT NULL,
  impact_hash TEXT NOT NULL,
  rule_count INT NOT NULL DEFAULT 0,
  rule_labels_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consumed_at TEXT,
  query_example_count INT NOT NULL DEFAULT 0
);

CREATE TABLE agent_thread_deletion_operations (
  operation_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  thread_id TEXT NOT NULL,
  data_source_id TEXT NOT NULL,
  journal_sequence INT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  request_hash TEXT
);

CREATE TABLE agent_thread_list_revisions (
  owner_user_id TEXT PRIMARY KEY,
  revision INT NOT NULL DEFAULT 1 CHECK (revision > 0)
);

CREATE TABLE auth_audit_events (
  id TEXT PRIMARY KEY,
  actor_user_id TEXT,
  target_user_id TEXT,
  action TEXT NOT NULL,
  request_id TEXT,
  summary_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE auth_login_throttles (
  username_key TEXT PRIMARY KEY,
  failed_count INT NOT NULL,
  window_started_at TEXT NOT NULL,
  blocked_until TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE auth_schema_meta (
  id INT PRIMARY KEY CHECK (id = 1),
  schema_version INT NOT NULL
);

CREATE TABLE auth_users (
  id TEXT PRIMARY KEY,
  username TEXT NOT NULL,
  username_key TEXT NOT NULL UNIQUE,
  role TEXT NOT NULL CHECK (role IN ('admin', 'member')),
  password_hash TEXT NOT NULL,
  is_active INT NOT NULL DEFAULT 1,
  must_change_password INT NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_login_at TEXT
);

CREATE TABLE business_rule_candidate_events (
  event_id TEXT PRIMARY KEY,
  business_rule_id TEXT NOT NULL,
  data_source_id TEXT NOT NULL,
  actor_user_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  previous_review_status TEXT,
  review_status TEXT NOT NULL,
  previous_publication_status TEXT,
  publication_status TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE model_profiles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key_ciphertext BYTEA,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  context_window_tokens INT,
  max_output_tokens INT,
  tokenizer_id TEXT
);

CREATE TABLE model_settings (
  id INT PRIMARY KEY CHECK (id = 1),
  provider TEXT NOT NULL,
  model TEXT NOT NULL,
  base_url TEXT NOT NULL,
  api_key_ciphertext BYTEA,
  updated_at TEXT NOT NULL
);

CREATE TABLE query_example_candidate_events (
  event_id TEXT PRIMARY KEY,
  query_example_id TEXT NOT NULL,
  data_source_id TEXT NOT NULL,
  actor_user_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  previous_review_status TEXT,
  review_status TEXT NOT NULL,
  previous_publication_status TEXT,
  publication_status TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  reason_code TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE schema_migrations (
  migration_id TEXT PRIMARY KEY,
  checksum TEXT NOT NULL,
  applied_at TEXT NOT NULL
);

CREATE TABLE wren_data_sources (
  id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  connector_type TEXT NOT NULL,
  enabled INT NOT NULL DEFAULT 1,
  active_revision_id TEXT,
  draft_revision_id TEXT,
  runtime_status TEXT NOT NULL DEFAULT 'not_ready',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES auth_users (
    id
  ),
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  idle_expires_at TEXT NOT NULL,
  absolute_expires_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE auth_user_data_sources (
  user_id TEXT NOT NULL REFERENCES auth_users (
    id
  ),
  data_source_id TEXT NOT NULL,
  granted_by TEXT NOT NULL REFERENCES auth_users (
    id
  ),
  granted_at TEXT NOT NULL,
  PRIMARY KEY (user_id, data_source_id)
);

CREATE TABLE business_rule_origins (
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  business_rule_id TEXT NOT NULL CHECK (LENGTH(business_rule_id) = 32),
  source_thread_id TEXT,
  source_thread_hash TEXT,
  term_label TEXT,
  content_hash TEXT NOT NULL,
  active_wren_revision_id TEXT,
  publication_status TEXT NOT NULL CHECK (publication_status IN ('active', 'removal_pending', 'removed')),
  published_at TEXT NOT NULL,
  redacted_at TEXT,
  purge_after TEXT,
  PRIMARY KEY (data_source_id, business_rule_id)
);

CREATE TABLE chat_thread_data_sources (
  thread_id TEXT PRIMARY KEY,
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  created_at TEXT NOT NULL,
  owner_user_id TEXT
);

CREATE TABLE model_settings_meta (
  id INT PRIMARY KEY CHECK (id = 1),
  default_profile_id TEXT,
  FOREIGN KEY (default_profile_id) REFERENCES model_profiles (
    id
  )
);

CREATE TABLE query_corpus_operations (
  operation_id TEXT PRIMARY KEY,
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  operation_type TEXT NOT NULL CHECK (operation_type IN ('approve', 'activate', 'revoke')),
  base_revision INT,
  target_revision INT,
  generation INT NOT NULL,
  actor_user_id TEXT NOT NULL,
  item_id TEXT,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('prepared', 'active', 'failed', 'cancelled')),
  failure_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE query_corpus_revisions (
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  corpus_revision INT NOT NULL CHECK (corpus_revision > 0),
  connector_type TEXT NOT NULL,
  wren_revision_id TEXT NOT NULL,
  mdl_digest TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  record_ids_json TEXT NOT NULL,
  canonical_path TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('prepared', 'active', 'superseded', 'failed', 'expired')),
  created_at TEXT NOT NULL,
  activated_at TEXT,
  superseded_at TEXT,
  delete_after TEXT,
  PRIMARY KEY (data_source_id, corpus_revision)
);

CREATE TABLE query_corpus_source_state (
  data_source_id TEXT PRIMARY KEY REFERENCES wren_data_sources (
    id
  ),
  active_revision INT,
  prepared_revision INT,
  generation INT NOT NULL DEFAULT 0 CHECK (generation >= 0),
  updated_at TEXT NOT NULL
);

CREATE TABLE wren_operations (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  revision_id TEXT,
  status TEXT NOT NULL,
  phase TEXT NOT NULL,
  error_code TEXT,
  message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  operation_type TEXT NOT NULL DEFAULT 'settings_apply',
  base_revision_id TEXT,
  base_mdl_digest TEXT,
  base_generation INT,
  target_revision_id TEXT,
  target_mdl_digest TEXT,
  activated_generation INT,
  actor_id TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE wren_revisions (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  status TEXT NOT NULL,
  config_json TEXT NOT NULL,
  project_dir TEXT,
  profile_name TEXT,
  mdl_digest TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  parent_revision_id TEXT,
  publication_operation_id TEXT
);

CREATE TABLE wren_source_operation_state (
  source_id TEXT PRIMARY KEY REFERENCES wren_data_sources (
    id
  ) ON DELETE CASCADE,
  generation INT NOT NULL DEFAULT 0,
  active_operation_id TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE wren_state (
  id INT PRIMARY KEY CHECK (id = 1),
  default_data_source_id TEXT REFERENCES wren_data_sources (
    id
  ),
  migration_status TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE agent_conversation_threads (
  thread_id TEXT PRIMARY KEY REFERENCES chat_thread_data_sources (
    thread_id
  ) ON DELETE CASCADE,
  status TEXT NOT NULL CHECK (status IN ('active', 'deleted', 'expired')),
  summary TEXT,
  summary_version INT NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  last_user_turn_at TEXT NOT NULL,
  deleted_at TEXT,
  tombstone_until TEXT,
  title TEXT,
  is_pinned INT NOT NULL DEFAULT 0,
  archived_at TEXT,
  metadata_revision INT NOT NULL DEFAULT 1
);

CREATE TABLE wren_secrets (
  source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  revision_id TEXT NOT NULL REFERENCES wren_revisions (
    id
  ),
  secret_name TEXT NOT NULL,
  ciphertext BYTEA NOT NULL,
  PRIMARY KEY (source_id, revision_id, secret_name)
);

CREATE TABLE agent_conversation_turns (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL REFERENCES agent_conversation_threads (
    thread_id
  ) ON DELETE CASCADE,
  sequence INT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  turn_id TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (
    thread_id,
    sequence
  ),
  UNIQUE (
    thread_id,
    turn_id,
    role
  )
);

CREATE TABLE agent_thread_history_imports (
  thread_id TEXT PRIMARY KEY REFERENCES agent_conversation_threads (
    thread_id
  ) ON DELETE CASCADE,
  import_id TEXT NOT NULL,
  expected_chunk_count INT NOT NULL CHECK (expected_chunk_count BETWEEN 1 AND 64),
  expected_turn_count INT NOT NULL CHECK (expected_turn_count BETWEEN 1 AND 500),
  expected_content_bytes INT NOT NULL CHECK (expected_content_bytes BETWEEN 1 AND 2097152),
  expected_chunk_hashes_json TEXT NOT NULL,
  received_chunks INT NOT NULL DEFAULT 0,
  received_turn_count INT NOT NULL DEFAULT 0,
  received_content_bytes INT NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE (
    thread_id,
    import_id
  )
);

CREATE TABLE agent_turn_requests (
  thread_id TEXT NOT NULL REFERENCES agent_conversation_threads (
    thread_id
  ) ON DELETE CASCADE,
  turn_id TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
  assistant_content TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  user_sequence INT,
  assistant_sequence INT,
  PRIMARY KEY (thread_id, turn_id)
);

CREATE TABLE business_rule_candidates (
  business_rule_id TEXT PRIMARY KEY CHECK (LENGTH(business_rule_id) = 32),
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  term TEXT,
  definition TEXT,
  mdl_references_json TEXT,
  source_thread_id TEXT REFERENCES agent_conversation_threads (
    thread_id
  ) ON DELETE SET NULL,
  base_wren_revision_id TEXT NOT NULL,
  base_mdl_digest TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  submitted_by TEXT NOT NULL,
  idempotency_hash TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  has_exact_term_conflict INT NOT NULL DEFAULT 0 CHECK (has_exact_term_conflict IN (0, 1)),
  review_status TEXT NOT NULL CHECK (review_status IN (
    'pending',
    'needs_clarification',
    'needs_revalidation',
    'approved',
    'rejected',
    'withdrawn',
    'revoked',
    'expired'
  )),
  publication_status TEXT NOT NULL CHECK (publication_status IN (
    'not_published',
    'queued',
    'publishing',
    'active',
    'failed',
    'removal_pending',
    'removed'
  )),
  clarification_question TEXT,
  reviewed_by TEXT,
  reviewed_at TEXT,
  review_reason_code TEXT,
  version INT NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  UNIQUE (
    submitted_by,
    idempotency_hash
  )
);

CREATE TABLE query_example_candidates (
  query_example_id TEXT PRIMARY KEY CHECK (LENGTH(query_example_id) = 32),
  data_source_id TEXT NOT NULL REFERENCES wren_data_sources (
    id
  ),
  source_thread_id TEXT REFERENCES agent_conversation_threads (
    thread_id
  ) ON DELETE SET NULL,
  source_turn_id TEXT,
  normalized_question TEXT,
  sql_template TEXT,
  parameter_specs_json TEXT,
  connector_type TEXT NOT NULL,
  wren_revision_id TEXT NOT NULL,
  mdl_digest TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  submitted_by TEXT NOT NULL,
  idempotency_hash TEXT NOT NULL,
  request_hash TEXT NOT NULL,
  review_status TEXT NOT NULL CHECK (review_status IN (
    'pending',
    'approved',
    'rejected',
    'needs_revalidation',
    'revoked',
    'withdrawn',
    'expired'
  )),
  publication_status TEXT NOT NULL CHECK (publication_status IN (
    'not_published',
    'queued',
    'publishing',
    'active',
    'failed',
    'superseded',
    'removed'
  )),
  reviewed_by TEXT,
  review_reason_code TEXT,
  version INT NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL,
  reviewed_at TEXT,
  activated_at TEXT,
  superseded_at TEXT,
  revoked_at TEXT,
  expires_at TEXT NOT NULL,
  source_thread_hash TEXT,
  source_turn_hash TEXT,
  UNIQUE (
    submitted_by,
    idempotency_hash
  )
);

CREATE TABLE agent_thread_history_import_chunks (
  thread_id TEXT NOT NULL,
  import_id TEXT NOT NULL,
  chunk_index INT NOT NULL CHECK (chunk_index >= 0),
  request_hash TEXT NOT NULL,
  turn_count INT NOT NULL CHECK (turn_count > 0),
  content_bytes INT NOT NULL CHECK (content_bytes >= 0),
  created_at TEXT NOT NULL,
  PRIMARY KEY (thread_id, import_id, chunk_index),
  FOREIGN KEY (thread_id, import_id) REFERENCES agent_thread_history_imports (
    thread_id,
    import_id
  ) ON DELETE CASCADE
);

CREATE INDEX idx_agent_delete_impacts_thread ON agent_thread_deletion_impacts(thread_id NULLS FIRST, owner_user_id NULLS FIRST, expires_at NULLS FIRST);

CREATE UNIQUE INDEX idx_agent_one_running_turn_per_thread ON agent_turn_requests(thread_id NULLS FIRST)
WHERE
  status = 'running';

CREATE INDEX idx_agent_suppressions_item ON agent_memory_suppressions(data_source_id NULLS FIRST, item_type NULLS FIRST, item_id NULLS FIRST);

CREATE INDEX idx_agent_thread_creation_thread ON agent_thread_creation_requests(thread_id NULLS FIRST);

CREATE INDEX idx_agent_threads_owner_activity ON chat_thread_data_sources(owner_user_id NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_agent_turns_thread_sequence ON agent_conversation_turns(thread_id NULLS FIRST, sequence NULLS FIRST);

CREATE INDEX idx_auth_audit_created ON auth_audit_events(created_at NULLS FIRST);

CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id NULLS FIRST, revoked_at NULLS FIRST);

CREATE INDEX idx_auth_user_data_sources_source ON auth_user_data_sources(data_source_id NULLS FIRST);

CREATE INDEX idx_business_rule_candidates_source_status ON business_rule_candidates(data_source_id NULLS FIRST, review_status NULLS FIRST, publication_status NULLS FIRST);

CREATE INDEX idx_business_rule_candidates_thread ON business_rule_candidates(data_source_id NULLS FIRST, source_thread_id NULLS FIRST);

CREATE INDEX idx_business_rule_events_rule ON business_rule_candidate_events(data_source_id NULLS FIRST, business_rule_id NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_business_rule_origins_thread ON business_rule_origins(data_source_id NULLS FIRST, source_thread_id NULLS FIRST, publication_status NULLS FIRST);

CREATE INDEX idx_chat_thread_owner_source ON chat_thread_data_sources(owner_user_id NULLS FIRST, data_source_id NULLS FIRST, thread_id NULLS FIRST);

CREATE INDEX idx_query_corpus_operations_source ON query_corpus_operations(data_source_id NULLS FIRST, status NULLS FIRST, generation NULLS FIRST);

CREATE INDEX idx_query_corpus_retention ON query_corpus_revisions(status NULLS FIRST, delete_after NULLS FIRST);

CREATE INDEX idx_query_example_events_item ON query_example_candidate_events(data_source_id NULLS FIRST, query_example_id NULLS FIRST, created_at NULLS FIRST);

CREATE INDEX idx_query_examples_source_state ON query_example_candidates(data_source_id NULLS FIRST, review_status NULLS FIRST, publication_status NULLS FIRST);

CREATE INDEX idx_query_examples_thread ON query_example_candidates(data_source_id NULLS FIRST, source_thread_id NULLS FIRST);

CREATE UNIQUE INDEX idx_wren_one_running_operation_per_source ON wren_operations(source_id NULLS FIRST)
WHERE
  status = 'running';

CREATE INDEX idx_wren_revisions_source ON wren_revisions(source_id NULLS FIRST, created_at NULLS FIRST);


CREATE FUNCTION askdb_casefold(value TEXT)
       RETURNS TEXT LANGUAGE sql IMMUTABLE PARALLEL SAFE
       AS $$ SELECT lower(COALESCE(value, '')) $$;

CREATE FUNCTION askdb_bump_thread_list_revision_on_source_rename()
       RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
           IF OLD.display_name IS DISTINCT FROM NEW.display_name THEN
               UPDATE agent_thread_list_revisions
               SET revision = revision + 1
               WHERE owner_user_id IN (
                   SELECT DISTINCT owner_user_id FROM chat_thread_data_sources
                   WHERE data_source_id = NEW.id
               );
           END IF;
           RETURN NEW;
       END;
       $$;

CREATE TRIGGER agent_thread_source_name_revision
       AFTER UPDATE OF display_name ON wren_data_sources
       FOR EACH ROW EXECUTE FUNCTION askdb_bump_thread_list_revision_on_source_rename();

CREATE FUNCTION askdb_reject_audit_event_mutation()
       RETURNS trigger LANGUAGE plpgsql AS $$
       BEGIN
           RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
       END;
       $$;

CREATE TRIGGER business_rule_events_no_update
       BEFORE UPDATE ON business_rule_candidate_events FOR EACH ROW
       EXECUTE FUNCTION askdb_reject_audit_event_mutation();

CREATE TRIGGER business_rule_events_no_delete
       BEFORE DELETE ON business_rule_candidate_events FOR EACH ROW
       EXECUTE FUNCTION askdb_reject_audit_event_mutation();

CREATE TRIGGER query_example_events_no_update
       BEFORE UPDATE ON query_example_candidate_events FOR EACH ROW
       EXECUTE FUNCTION askdb_reject_audit_event_mutation();

CREATE TRIGGER query_example_events_no_delete
       BEFORE DELETE ON query_example_candidate_events FOR EACH ROW
       EXECUTE FUNCTION askdb_reject_audit_event_mutation();
