from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from cryptography.fernet import Fernet

from application.wren_settings import WrenSettingsApplication
from application.lexical_recall import lexical_recall
from config import Settings
from integrations.mysql_schema import ForeignKeySchema, MysqlSchemaReader, TableSchema
from integrations.wren_cli import WrenCli
from integrations.wren_memory import load_semantic_recall_documents
from integrations.wren_project import WrenProjectBuilder
from wren_settings import WrenConfigurationError
from wren_settings import WrenSettingsStore


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


def test_semantic_config_rejects_duplicate_rule_names_after_normalization():
    with pytest.raises(WrenConfigurationError, match="规则名称不能重复"):
        WrenSettingsApplication._clean_semantic({
            "rules": [
                {"name": "异常地区", "content": "definition one"},
                {"name": "  异常地区  ", "content": "definition two"},
            ],
        })


def test_semantic_config_rejects_compatibility_and_case_duplicates():
    with pytest.raises(WrenConfigurationError, match="规则名称不能重复"):
        WrenSettingsApplication._clean_semantic({
            "rules": [
                {"name": "Revenue Rule", "content": "definition one"},
                {"name": "ＲＥＶＥＮＵＥ   rule", "content": "definition two"},
            ],
        })


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


def test_schema_refresh_returns_foreign_key_metadata(tmp_path, postgres_database):
    store = WrenSettingsStore(postgres_database, Fernet.generate_key().decode())

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


def test_chinese_rule_names_survive_project_build_and_lexical_recall(tmp_path):
    builder = WrenProjectBuilder(tmp_path)
    project = builder.build(
        "source-a",
        "rev-rules",
        "profile-a",
        "mysql",
        {
            "database": "analytics",
            "tables": ["orders"],
            "rules": [
                {"name": "异常地区", "content": "地区名称等于销售区域-02-地区-01"},
                {"name": "异常省份", "content": "省份名称等于销售区域-02-省份-01"},
            ],
        },
        [TableSchema(name="orders", columns=[], foreign_keys=[])],
    )
    (project / "target").mkdir(parents=True)
    (project / "target" / "mdl.json").write_text("{}", encoding="utf-8")

    rule_files = sorted((project / "knowledge" / "rules").glob("*.md"))
    documents = load_semantic_recall_documents(
        project,
        data_source_id="source-a",
        wren_revision_id="rev-rules",
        connector_type="mysql",
        mdl_digest="digest-rules",
    )
    hits = lexical_recall(
        documents,
        data_source_id="source-a",
        connector_type="mysql",
        wren_revision_id="rev-rules",
        mdl_digest="digest-rules",
        query="异常地区有哪些",
    )

    assert len(rule_files) == 2
    assert {document.title for document in documents if document.kind.value == "business_rule"} == {
        "异常地区",
        "异常省份",
    }
    assert any(
        hit.document.title == "异常地区" and "异常" in hit.matched_terms
        for hit in hits
    )


def test_legacy_rule_file_uses_configured_chinese_name_for_recall(tmp_path):
    project = tmp_path / "legacy-project"
    rules_dir = project / "knowledge" / "rules"
    rules_dir.mkdir(parents=True)
    (project / "target").mkdir()
    (project / "target" / "mdl.json").write_text("{}", encoding="utf-8")
    (rules_dir / "rule.md").write_text(
        "地区名称等于销售区域-02-地区-01\n", encoding="utf-8"
    )

    documents = load_semantic_recall_documents(
        project,
        data_source_id="source-a",
        wren_revision_id="rev-legacy",
        connector_type="mysql",
        mdl_digest="digest-legacy",
        configured_rules=[
            {"name": "异常地区", "content": "地区名称等于销售区域-02-地区-01"}
        ],
    )
    hits = lexical_recall(
        documents,
        data_source_id="source-a",
        connector_type="mysql",
        wren_revision_id="rev-legacy",
        mdl_digest="digest-legacy",
        query="异常地区有哪些",
    )

    assert len(documents) == 1
    assert documents[0].title == "异常地区"
    assert any("异常" in hit.matched_terms for hit in hits)


def test_project_builder_keeps_managed_rule_filename_and_human_title(tmp_path):
    managed_id = "a" * 32
    project = WrenProjectBuilder(tmp_path).build(
        "source-a",
        "rev-managed",
        "profile-a",
        "mysql",
        {
            "database": "analytics",
            "tables": ["orders"],
            "rules": [
                {
                    "name": f"askdb_br_{managed_id}",
                    "content": "# 异常地区\n地区名称等于销售区域-02-地区-01",
                }
            ],
        },
        [TableSchema(name="orders", columns=[], foreign_keys=[])],
    )
    rule_file = project / "knowledge" / "rules" / f"askdb_br_{managed_id}.md"

    assert rule_file.is_file()
    assert rule_file.read_text(encoding="utf-8").startswith("# 异常地区\n")


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


def test_initializes_legacy_wren_as_encrypted_default_source(tmp_path, postgres_database):
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
    store = WrenSettingsStore(postgres_database, key)
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
