from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from integrations.database import PostgresConnection


_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_MIGRATION_FILENAME = re.compile(r"(?P<version>[0-9]{3,})_[a-z][a-z0-9_]*\.sql\Z")


@dataclass(frozen=True)
class _Migration:
    version: int
    migration_id: str
    source: bytes
    checksum: str


def _load_migrations(directory: Path = _MIGRATIONS_DIR) -> tuple[_Migration, ...]:
    """Load ordered SQL migrations and reject ambiguous or incomplete histories."""
    paths = sorted(directory.glob("*.sql"))
    if not paths:
        raise RuntimeError("No PostgreSQL migrations are available")

    migrations: list[_Migration] = []
    seen_versions: set[int] = set()
    for path in paths:
        match = _MIGRATION_FILENAME.fullmatch(path.name)
        if match is None:
            raise RuntimeError(f"Invalid PostgreSQL migration filename: {path.name}")
        version = int(match.group("version"))
        if version in seen_versions:
            raise RuntimeError(f"Duplicate PostgreSQL migration version: {version:03d}")
        seen_versions.add(version)
        source = path.read_bytes()
        migrations.append(
            _Migration(
                version=version,
                migration_id=path.stem,
                source=source,
                checksum=hashlib.sha256(source).hexdigest(),
            )
        )

    if migrations[0].version != 1:
        raise RuntimeError("PostgreSQL migrations must start at version 001")
    expected_versions = list(range(1, len(migrations) + 1))
    actual_versions = [migration.version for migration in migrations]
    if actual_versions != expected_versions:
        raise RuntimeError("PostgreSQL migration versions must be contiguous")
    return tuple(migrations)


def apply_postgres_migrations(connection: PostgresConnection) -> None:
    """Apply each checked-in migration once, rejecting drift and unmanaged schemas."""
    migrations = _load_migrations()
    connection.acquire_write_lock()
    connection.execute(
        """CREATE TABLE IF NOT EXISTS app_schema_migrations (
               migration_id TEXT PRIMARY KEY,
               checksum TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )"""
    )

    applied_rows = connection.execute(
        "SELECT migration_id, checksum FROM app_schema_migrations"
    ).fetchall()
    applied = {row["migration_id"]: row["checksum"] for row in applied_rows}
    migration_ids = {migration.migration_id for migration in migrations}
    unknown_ids = set(applied) - migration_ids
    if unknown_ids:
        unknown_id = sorted(unknown_ids)[0]
        raise RuntimeError(f"Unknown PostgreSQL migration recorded: {unknown_id}")

    missing_seen = False
    for migration in migrations:
        recorded_checksum = applied.get(migration.migration_id)
        if recorded_checksum is None:
            missing_seen = True
            continue
        if missing_seen:
            raise RuntimeError("PostgreSQL migrations are not applied in order")
        if recorded_checksum != migration.checksum:
            raise RuntimeError(
                f"PostgreSQL migration checksum mismatch: {migration.migration_id}"
            )

    if not applied:
        existing_tables = connection.execute(
            """SELECT table_name FROM information_schema.tables
               WHERE table_schema = current_schema()
                 AND table_type = 'BASE TABLE'
                 AND table_name <> 'app_schema_migrations'
               LIMIT 1"""
        ).fetchone()
        if existing_tables is not None:
            raise RuntimeError("PostgreSQL schema is not empty and has no migration record")

    for migration in migrations:
        if migration.migration_id in applied:
            continue
        connection.execute_script(migration.source.decode("utf-8"))
        connection.execute(
            """INSERT INTO app_schema_migrations(migration_id, checksum, applied_at)
               VALUES (%s, %s, %s)""",
            (
                migration.migration_id,
                migration.checksum,
                datetime.now(UTC).isoformat(),
            ),
        )
