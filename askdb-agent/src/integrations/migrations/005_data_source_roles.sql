CREATE TABLE auth_data_source_roles (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  name_key TEXT NOT NULL UNIQUE,
  description TEXT NOT NULL DEFAULT '',
  version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE auth_user_role_bindings (
  user_id TEXT NOT NULL REFERENCES auth_users (id),
  role_id TEXT NOT NULL REFERENCES auth_data_source_roles (id),
  bound_by TEXT NOT NULL REFERENCES auth_users (id),
  bound_at TEXT NOT NULL,
  PRIMARY KEY (user_id, role_id)
);

CREATE INDEX idx_auth_user_role_bindings_role ON auth_user_role_bindings(role_id);

CREATE TABLE auth_role_data_sources (
  role_id TEXT NOT NULL REFERENCES auth_data_source_roles (id),
  data_source_id TEXT NOT NULL,
  granted_by TEXT NOT NULL REFERENCES auth_users (id),
  granted_at TEXT NOT NULL,
  PRIMARY KEY (role_id, data_source_id)
);

CREATE INDEX idx_auth_role_data_sources_source ON auth_role_data_sources(data_source_id);
