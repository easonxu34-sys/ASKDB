from __future__ import annotations

from cryptography.fernet import Fernet

from application.effective_permissions import EffectivePermissionsApplication
from auth_store import AuthStore
from integrations.data_source_role_store import DataSourceRoleStore
from integrations.database import PostgresDatabase
from integrations.effective_permission_store import EffectivePermissionStore
from wren_settings import WrenSettingsStore


def _ready_source(store: WrenSettingsStore, name: str, source_id: str):
    source = store.create_data_source(name, "mysql", {}, source_id=source_id)
    return store.update_runtime_status(source.id, "ready")


def test_user_and_source_views_share_grant_paths_and_stable_pagination(postgres_dsn: str) -> None:
    database = PostgresDatabase(postgres_dsn)
    auth = AuthStore(database)
    admin = auth.create_bootstrap_admin(
        user_id="permission-admin",
        username="permission-admin",
        username_key="permission-admin",
        password_hash="$argon2id$test-only",
    )
    sources = WrenSettingsStore(database, Fernet.generate_key().decode())
    finance = _ready_source(sources, "Finance", "permission-finance")
    sales = _ready_source(sources, "Sales", "permission-sales")

    role_store = DataSourceRoleStore(database)
    role_a = role_store.create_role(
        name="Finance readers",
        name_key="finance readers",
        description="",
        source_ids=[finance.id],
        actor_id=admin.id,
        request_id=None,
    )
    role_b = role_store.create_role(
        name="Monthly reports",
        name_key="monthly reports",
        description="",
        source_ids=[finance.id],
        actor_id=admin.id,
        request_id=None,
    )
    member = auth.create_user(
        user_id="permission-member",
        username="permission-member",
        username_key="permission-member",
        role="member",
        password_hash="$argon2id$test-only",
        actor_user_id=admin.id,
        role_ids=[role_a["id"], role_b["id"]],
    )
    auth.grant_data_source(member.id, finance.id, actor_user_id=admin.id)

    application = EffectivePermissionsApplication(EffectivePermissionStore(database))
    first_page = application.query(
        view="user",
        user_id=member.id,
        data_source_id=None,
        scope="all",
        status="all",
        limit=1,
        cursor=None,
    )
    second_page = application.query(
        view="user",
        user_id=member.id,
        data_source_id=None,
        scope="all",
        status="all",
        limit=1,
        cursor=first_page["next_cursor"],
    )
    source_view = application.query(
        view="source",
        user_id=None,
        data_source_id=finance.id,
        scope="all",
        status="all",
        limit=20,
        cursor=None,
    )

    user_items = first_page["items"] + second_page["items"]
    assert len(user_items) == 2
    assert first_page["next_cursor"] is not None
    assert second_page["next_cursor"] is None
    assert {item["data_source"]["id"] for item in user_items} == {finance.id, sales.id}
    assert first_page["counts"] == {
        "total": 2,
        "usable": 1,
        "granted_blocked": 0,
        "ungranted": 1,
        "multiple_sources": 1,
        "matching": 2,
    }

    user_permission = next(item for item in user_items if item["data_source"]["id"] == finance.id)
    source_permission = next(
        item for item in source_view["items"] if item["user"]["id"] == member.id
    )
    assert user_permission == source_permission
    assert [item["type"] for item in user_permission["grant_sources"]] == [
        "direct",
        "role",
        "role",
    ]
    assert {item["role_id"] for item in user_permission["grant_sources"] if item["type"] == "role"} == {
        role_a["id"],
        role_b["id"],
    }


def test_cursor_cannot_be_reused_with_different_filters(postgres_dsn: str) -> None:
    from integrations.effective_permission_store import EffectivePermissionStoreError

    database = PostgresDatabase(postgres_dsn)
    auth = AuthStore(database)
    auth.create_bootstrap_admin(
        user_id="cursor-admin",
        username="cursor-admin",
        username_key="cursor-admin",
        password_hash="$argon2id$test-only",
    )
    source_store = WrenSettingsStore(database, Fernet.generate_key().decode())
    _ready_source(source_store, "One", "cursor-source-1")
    _ready_source(source_store, "Two", "cursor-source-2")
    store = EffectivePermissionStore(database)

    page = store.query(
        view="user",
        user_id="cursor-admin",
        data_source_id=None,
        scope="all",
        status="all",
        limit=1,
        cursor=None,
    )

    try:
        store.query(
            view="user",
            user_id="cursor-admin",
            data_source_id=None,
            scope="all",
            status="usable",
            limit=1,
            cursor=page["next_cursor"],
        )
    except EffectivePermissionStoreError as exc:
        assert exc.code == "CURSOR_INVALID"
        assert exc.status_code == 400
    else:
        raise AssertionError("cursor with different status filter should be rejected")
