from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo
from cryptography.fernet import Fernet


def test_postgres_connection_handles_native_parameters_and_row_access(postgres_dsn: str) -> None:
    from integrations.database import PostgresDatabase

    database = PostgresDatabase(postgres_dsn)
    with database.connect() as connection:
        row = connection.execute("SELECT '?' AS literal, %s::integer AS value", (7,)).fetchone()

    assert row["literal"] == "?"
    assert row[1] == 7


def test_postgres_connection_context_rolls_back_on_exception(postgres_dsn: str) -> None:
    from integrations.database import PostgresDatabase

    database = PostgresDatabase(postgres_dsn)
    with pytest.raises(RuntimeError, match="rollback probe"):
        with database.connect() as connection:
            connection.execute("CREATE TABLE rollback_probe (id integer PRIMARY KEY)")
            raise RuntimeError("rollback probe")

    with database.connect() as connection:
        exists = connection.execute(
            "SELECT to_regclass('rollback_probe') AS table_name"
        ).fetchone()["table_name"]

    assert exists is None


def test_postgres_schema_migrations_are_idempotent(postgres_dsn: str) -> None:
    from integrations.database import PostgresDatabase
    from integrations.postgres_migrations import apply_postgres_migrations

    database = PostgresDatabase(postgres_dsn)
    with database.connect() as connection:
        apply_postgres_migrations(connection)
        apply_postgres_migrations(connection)
        tables = {
            row["table_name"]
            for row in connection.execute(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema = current_schema()"""
            ).fetchall()
        }
        running_turn_index = connection.execute(
            """SELECT indexdef FROM pg_indexes
               WHERE schemaname = current_schema()
                 AND indexname = 'idx_agent_one_running_turn_per_thread'"""
        ).fetchone()

    assert {
        "auth_users",
        "model_profiles",
        "wren_data_sources",
        "agent_turn_requests",
    } <= tables
    assert running_turn_index is not None
    assert "WHERE (status = 'running'" in running_turn_index["indexdef"]


def test_postgres_migrations_upgrade_an_existing_initial_schema(postgres_dsn: str) -> None:
    from integrations.database import PostgresConnection, PostgresDatabase

    initial_migration = Path(__file__).parents[1] / "src/integrations/migrations/001_initial.sql"
    source = initial_migration.read_bytes()
    checksum = hashlib.sha256(source).hexdigest()

    with psycopg.connect(postgres_dsn, autocommit=True) as raw_connection:
        connection = PostgresConnection(raw_connection)
        connection.execute(
            """CREATE TABLE app_schema_migrations (
                   migration_id TEXT PRIMARY KEY,
                   checksum TEXT NOT NULL,
                   applied_at TEXT NOT NULL
               )"""
        )
        connection.execute_script(source.decode("utf-8"))
        connection.execute(
            """INSERT INTO app_schema_migrations(migration_id, checksum, applied_at)
               VALUES (%s, %s, %s)""",
            ("001_initial", checksum, "2026-10-07T00:00:00+00:00"),
        )
        connection.execute(
            """INSERT INTO auth_users(
                   id, username, username_key, role, password_hash, created_at, updated_at
               ) VALUES (%s, %s, %s, %s, %s, %s, %s)""",
            (
                "upgrade-sentinel",
                "migration-sentinel",
                "migration-sentinel",
                "admin",
                "$argon2id$test-only",
                "2026-10-07T00:00:00+00:00",
                "2026-10-07T00:00:00+00:00",
            ),
        )

    with PostgresDatabase(postgres_dsn).connect() as connection:
        sentinel = connection.execute(
            "SELECT username FROM auth_users WHERE id = %s", ("upgrade-sentinel",)
        ).fetchone()
        migration_ids = {
            row["migration_id"]
            for row in connection.execute(
                "SELECT migration_id FROM app_schema_migrations ORDER BY migration_id"
            ).fetchall()
        }
        vector_type = connection.execute(
            "SELECT to_regtype('vector') AS vector_type"
        ).fetchone()

    assert sentinel["username"] == "migration-sentinel"
    assert migration_ids == {"001_initial", "002_enable_pgvector"}
    assert vector_type["vector_type"] is not None


def test_postgres_fresh_start_and_repeated_start_apply_each_migration_once(
    postgres_dsn: str,
) -> None:
    from integrations.database import PostgresDatabase

    for _ in range(2):
        with PostgresDatabase(postgres_dsn).connect() as connection:
            migration_rows = connection.execute(
                """SELECT migration_id, COUNT(*) AS row_count
                   FROM app_schema_migrations GROUP BY migration_id"""
            ).fetchall()
            tables = {
                row["table_name"]
                for row in connection.execute(
                    """SELECT table_name FROM information_schema.tables
                       WHERE table_schema = current_schema()
                         AND table_type = 'BASE TABLE'"""
                ).fetchall()
            }
            vector_type = connection.execute(
                "SELECT to_regtype('vector') AS vector_type"
            ).fetchone()

        assert {row["migration_id"] for row in migration_rows} == {
            "001_initial",
            "002_enable_pgvector",
        }
        assert all(row["row_count"] == 1 for row in migration_rows)
        assert {
            "auth_users",
            "model_profiles",
            "wren_data_sources",
            "agent_turn_requests",
        } <= tables
        assert vector_type["vector_type"] is not None


def test_postgres_migrations_reject_a_changed_recorded_checksum(postgres_dsn: str) -> None:
    from integrations.database import PostgresDatabase

    with PostgresDatabase(postgres_dsn).connect():
        pass

    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute(
            "UPDATE app_schema_migrations SET checksum = %s WHERE migration_id = %s",
            ("changed-checksum", "001_initial"),
        )

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        PostgresDatabase(postgres_dsn).connect()


def test_postgres_migrations_reject_an_unknown_recorded_migration(postgres_dsn: str) -> None:
    from integrations.database import PostgresDatabase

    with PostgresDatabase(postgres_dsn).connect():
        pass

    with psycopg.connect(postgres_dsn, autocommit=True) as connection:
        connection.execute(
            """INSERT INTO app_schema_migrations(migration_id, checksum, applied_at)
               VALUES (%s, %s, %s)""",
            ("999_unreleased", "unknown", "2026-10-07T00:00:00+00:00"),
        )

    with pytest.raises(RuntimeError, match="Unknown PostgreSQL migration"):
        PostgresDatabase(postgres_dsn).connect()


def test_postgres_migration_discovery_rejects_version_gaps(tmp_path: Path) -> None:
    from integrations.postgres_migrations import _load_migrations

    (tmp_path / "001_initial.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "003_missing_version.sql").write_text("SELECT 3;", encoding="utf-8")

    with pytest.raises(RuntimeError, match="contiguous"):
        _load_migrations(tmp_path)


def test_postgres_migration_discovery_rejects_unversioned_sql(tmp_path: Path) -> None:
    from integrations.postgres_migrations import _load_migrations

    (tmp_path / "initial.sql").write_text("SELECT 1;", encoding="utf-8")

    with pytest.raises(RuntimeError, match="filename"):
        _load_migrations(tmp_path)


def test_postgres_turn_start_serializes_different_turns_for_one_thread(
    postgres_dsn: str,
    tmp_path,
) -> None:
    from auth_store import AuthStore
    from domain.conversation_memory import TurnAlreadyRunning
    from integrations.business_rule_store import BusinessRuleMemoryStore
    from integrations.conversation_store import ConversationMemoryStore
    from integrations.database import PostgresDatabase
    from integrations.deletion_journal import EncryptedDeletionJournal
    from integrations.query_memory_store import QueryMemoryStore
    from model_settings import ModelConfiguration, ModelSettingsStore
    from wren_settings import WrenSettingsStore

    database = PostgresDatabase(postgres_dsn)
    auth = AuthStore(database)
    auth.initialize()
    auth.create_bootstrap_admin(
        user_id="admin-test",
        username="admin",
        username_key="admin",
        password_hash="$argon2id$test-only",
    )
    source = WrenSettingsStore(database, Fernet.generate_key().decode()).create_data_source(
        "PostgreSQL test source", "mysql", {}
    )
    model_store = ModelSettingsStore(database, Fernet.generate_key().decode())
    profile = model_store.create(
        ModelConfiguration("custom", "test-model", "https://example.test/v1", "test-key"),
        "PostgreSQL test profile",
        make_default=True,
    )
    assert model_store.get_default().id == profile.id
    store = ConversationMemoryStore(database)
    thread = store.create_thread(owner_user_id="admin-test", source_id=source.id)
    page = store.list_thread_page(owner_user_id="admin-test", view="recent", q="postgres")
    assert [item.thread_id for item in page.threads] == [thread.thread_id]

    journal = EncryptedDeletionJournal(
        tmp_path / "memory" / "deletion-journal.jsonl",
        Fernet.generate_key().decode(),
    )
    business_rules = BusinessRuleMemoryStore(database, deletion_journal=journal)
    query_memory = QueryMemoryStore(
        database,
        corpus_root=tmp_path / "corpus",
        deletion_journal=journal,
    )
    query_memory.initialize()
    assert business_rules.expire_candidates(limit=1) == 0
    assert query_memory.expire_pending_candidates(limit=1) == 0
    assert query_memory.prune_expired_revisions(limit=1) == 0

    def begin(turn_id: str):
        return store.begin_turn(
            thread_id=thread.thread_id,
            owner_user_id="admin-test",
            turn_id=turn_id,
            user_content=f"question for {turn_id}",
            expected_sequence=0,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = []
        for future in (executor.submit(begin, "turn-a"), executor.submit(begin, "turn-b")):
            try:
                outcomes.append(future.result())
            except TurnAlreadyRunning:
                outcomes.append("already-running")

    assert sum(result == "already-running" for result in outcomes) == 1
    assert sum(getattr(result, "is_new", False) for result in outcomes) == 1
