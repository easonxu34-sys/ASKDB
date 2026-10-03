from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from cryptography.fernet import Fernet

from askdb_agent.application.wren_settings import WrenSettingsApplication
from askdb_agent.config import Settings
from askdb_agent.integrations.mysql_schema import ForeignKeySchema, MysqlSchemaReader, TableSchema
from askdb_agent.integrations.wren_cli import WrenCli
from askdb_agent.integrations.wren_project import WrenProjectBuilder
from askdb_agent.wren_settings import WrenConfigurationError
from askdb_agent.wren_settings import WrenSettingsStore


class FakeCommandRunner:
    def __init__(self, *, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs.get("cwd"), kwargs))
        return SimpleNamespace(stdout=self.stdout, stderr=self.stderr, returncode=self.returncode)


def test_cli_uses_argument_array_and_revision_working_directory(tmp_path):
    runner = FakeCommandRunner(stdout="")
    project = tmp_path / "sources" / "ds-a" / "revisions" / "rev-1"
    project.mkdir(parents=True)

    WrenCli(runner, tmp_path / "wren-home").build(project)

    assert runner.calls[0][0] == ["wren", "context", "build"]
    assert runner.calls[0][1] == project
    assert runner.calls[0][2]["shell"] is False


def test_failed_cli_output_is_redacted(tmp_path):
    runner = FakeCommandRunner(stderr="connection failed for password secret-marker", returncode=1)
    cli = WrenCli(runner, tmp_path / "wren-home")

    with pytest.raises(WrenConfigurationError) as error:
        cli.build(tmp_path, secrets_to_redact=["secret-marker"])

    assert "secret-marker" not in str(error.value)


class FakeCursor:
    def __init__(self):
        self.statements = []

    def execute(self, statement, params=None):
        self.statements.append(statement)

    def fetchall(self):
        if "COLUMNS" in self.statements[-1]:
            return [("id", "int", "NO", "PRI"), ("title", "varchar(80)", "YES", "")]
        if "KEY_COLUMN_USAGE" in self.statements[-1]:
            return [("fk_orders_customers", "customer_id", "customers", "id")]
        return [("orders",)]

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_value = FakeCursor()
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def close(self):
        self.closed = True


def test_schema_reader_only_returns_metadata():
    connection = FakeConnection()
    reader = MysqlSchemaReader(
        {"host": "db.internal", "port": 3306, "database": "analytics", "user": "reader"},
        {"password": "secret-marker"},
        connector_factory=lambda **_kwargs: connection,
    )

    tables = reader.introspect()

    assert tables[0].name == "orders"
    assert [column.name for column in tables[0].columns] == ["id", "title"]
    assert tables[0].foreign_keys[0].referenced_table == "customers"
    assert tables[0].foreign_keys[0].column == "customer_id"
    assert all("SELECT *" not in statement.upper() for statement in connection.cursor_value.statements)
    assert connection.closed


def test_schema_refresh_returns_foreign_key_metadata(tmp_path):
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", Fernet.generate_key().decode())

    class SchemaReader:
        def __init__(self, *_args):
            pass

        def introspect(self):
            return [TableSchema(
                name="orders",
                columns=[],
                foreign_keys=[ForeignKeySchema("fk_orders_customers", "customer_id", "customers", "id")],
            )]

    service = WrenSettingsApplication(
        store=store,
        model_store=SimpleNamespace(),
        settings=Settings(None, None, "openai:test", wren_home=tmp_path / "wren-home", wren_data_dir=tmp_path / "wren"),
        schema_reader_factory=SchemaReader,
    )
    source = service.create_source("分析库", {
        "host": "db.internal", "port": 3306, "database": "analytics", "user": "reader",
    })

    result = service.refresh_schema(source["data_source"]["id"])

    assert result["tables"][0]["foreign_keys"] == [{
        "name": "fk_orders_customers",
        "column": "customer_id",
        "referenced_table": "customers",
        "referenced_column": "id",
    }]


def test_project_builder_rejects_path_escape(tmp_path):
    builder = WrenProjectBuilder(tmp_path)

    with pytest.raises(WrenConfigurationError):
        builder.build("../escape", "rev-1", "profile-1", "mysql", {}, [])


def test_project_builder_does_not_include_every_table_when_selection_is_empty(tmp_path):
    builder = WrenProjectBuilder(tmp_path)
    schema = [SimpleNamespace(name="orders", columns=[SimpleNamespace(name="id", type="INT", nullable=False, primary_key=True)])]

    with pytest.raises(WrenConfigurationError, match="至少选择一个"):
        builder.build("source-a", "rev-empty", "profile-a", "mysql", {"database": "analytics", "tables": []}, schema)


def test_project_builder_creates_bound_project_without_secret(tmp_path):
    builder = WrenProjectBuilder(tmp_path)
    schema = [
        SimpleNamespace(
            name="orders",
            columns=[
                SimpleNamespace(name="id", type="INT", nullable=False, primary_key=True),
                SimpleNamespace(name="internal_note", type="VARCHAR(80)", nullable=True, primary_key=False),
            ],
        )
    ]

    project = builder.build(
        "source-a", "rev-1", "profile-a", "mysql",
        {
            "database": "analytics", "tables": ["orders"],
            "models": [{"table": "orders", "name": "Orders", "columns": [
                {"name": "internal_note", "hidden": True},
            ]}],
        }, schema,
    )

    assert (project / "wren_project.yml").is_file()
    model = yaml.safe_load((project / "models" / "Orders" / "metadata.yml").read_text(encoding="utf-8"))
    hidden_column = next(column for column in model["columns"] if column["name"] == "internal_note")
    assert hidden_column["is_hidden"] is True
    assert (project / "models" / "Orders" / "metadata.yml").is_file()
    assert "secret-marker" not in "".join(path.read_text() for path in project.rglob("*.*"))


class FakeMigrationCli:
    def __init__(self):
        self.profile_fields = None
        self.secret_values = None

    def add_profile(self, _profile_name, profile_fields, *, secret_values, secrets_to_redact):
        self.profile_fields = profile_fields
        self.secret_values = secret_values
        assert "legacy-secret-marker" not in str(profile_fields)
        assert "legacy-secret-marker" in secrets_to_redact

    def validate(self, project_dir, *, secrets_to_redact):
        assert (project_dir / "wren_project.yml").is_file()

    def build(self, project_dir, *, secrets_to_redact):
        assert (project_dir / "target" / "mdl.json").is_file()


def test_initializes_legacy_wren_as_encrypted_default_source(tmp_path):
    project = tmp_path / "legacy-project"
    (project / "target").mkdir(parents=True)
    (project / "wren_project.yml").write_text(
        "schema_version: 5\nname: legacy_project\ndata_source: mysql\nprofile: legacy-profile\nschema: analytics\n",
        encoding="utf-8",
    )
    (project / "target" / "mdl.json").write_text("{}", encoding="utf-8")
    (project / ".env").write_text("PASSWORD=must-not-be-copied", encoding="utf-8")
    legacy_home = tmp_path / "old-wren-home"
    legacy_home.mkdir()
    (legacy_home / "profiles.yml").write_text(
        yaml.safe_dump({"profiles": {"legacy-profile": {
            "datasource": "mysql", "host": "db.internal", "port": 3306,
            "database": "analytics", "user": "reader", "password": "legacy-secret-marker",
            "sslMode": "disabled",
        }}}),
        encoding="utf-8",
    )
    key = Fernet.generate_key().decode("ascii")
    store = WrenSettingsStore(tmp_path / "settings.sqlite3", key)
    cli = FakeMigrationCli()
    settings = Settings(
        wren_project_dir=project,
        wren_profile="legacy-profile",
        model="openai:test",
        wren_home=tmp_path / "new-wren-home",
        legacy_wren_home=legacy_home,
        wren_data_dir=tmp_path / "managed-wren",
    )
    service = WrenSettingsApplication(
        store=store,
        settings=settings,
        cli=cli,
        model_store=SimpleNamespace(),
    )

    asyncio.run(service.initialize())

    source = store.get_default_source()
    assert source is not None
    assert source.id == "ds_legacy_wren"
    assert source.active_revision_id == "rev_legacy_wren"
    assert store.get_secret(source.id, source.active_revision_id, "password") == "legacy-secret-marker"
    assert "legacy-secret-marker" not in str(store.source_detail(source.id))
    assert cli.profile_fields["password"].startswith("${ASKDB_WREN_")
    imported = Path(store.get_revision(source.id, source.active_revision_id).project_dir)
    assert not (imported / ".env").exists()
    manifest = yaml.safe_load((imported / "wren_project.yml").read_text(encoding="utf-8"))
    assert manifest["profile"] == "askdb_legacy_wren"
    assert (project / "wren_project.yml").read_text(encoding="utf-8").endswith("schema: analytics\n")
