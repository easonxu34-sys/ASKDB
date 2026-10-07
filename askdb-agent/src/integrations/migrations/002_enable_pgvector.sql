-- Install the extension when migrations run as a PostgreSQL superuser. The normal
-- application role must have an administrator enable it once per new database.
DO $migration$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') THEN
    RETURN;
  END IF;

  IF NOT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
    RAISE EXCEPTION
      'pgvector server files are unavailable; install pgvector for this PostgreSQL version';
  END IF;

  IF current_setting('is_superuser') <> 'on' THEN
    RAISE EXCEPTION
      'pgvector is available but not enabled; a PostgreSQL administrator must run CREATE EXTENSION vector WITH SCHEMA public';
  END IF;

  EXECUTE 'CREATE EXTENSION vector WITH SCHEMA public';
END;
$migration$;
