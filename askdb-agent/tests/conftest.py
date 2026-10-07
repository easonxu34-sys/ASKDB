from __future__ import annotations

import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import make_conninfo


@pytest.fixture
def postgres_dsn() -> str:
    source_dsn = os.environ.get("ASKDB_TEST_DATABASE_DSN")
    if not source_dsn:
        pytest.skip("ASKDB_TEST_DATABASE_DSN is required for PostgreSQL integration tests")

    schema = f"test_storage_{uuid.uuid4().hex}"
    with psycopg.connect(source_dsn, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    try:
        yield make_conninfo(source_dsn, options=f"-c search_path={schema},public")
    finally:
        with psycopg.connect(source_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


@pytest.fixture
def postgres_database(postgres_dsn):
    from integrations.database import PostgresDatabase

    return PostgresDatabase(postgres_dsn)
