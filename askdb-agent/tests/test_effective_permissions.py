from __future__ import annotations

import pytest

from application.effective_permissions import (
    explain_permission,
    preview_source_removal,
)


def _user(*, role: str = "member", active: bool = True) -> dict[str, object]:
    return {"id": "user-1", "username": "alice", "role": role, "is_active": active}


def _source(
    *, exists: bool = True, enabled: bool = True, runtime_status: str = "ready"
) -> dict[str, object]:
    return {
        "id": "source-1",
        "display_name": "Finance",
        "exists": exists,
        "enabled": enabled,
        "runtime_status": runtime_status,
    }


def _direct() -> dict[str, object]:
    return {"type": "direct", "granted_by": "admin-1", "granted_at": "2026-10-01T00:00:00+00:00"}


def _role(role_id: str, name: str, *, name_key: str | None = None) -> dict[str, object]:
    return {
        "type": "role",
        "role_id": role_id,
        "role_name": name,
        "role_name_key": name_key or name.casefold(),
        "bound_by": "admin-1",
        "bound_at": "2026-10-02T00:00:00+00:00",
        "granted_by": "admin-1",
        "granted_at": "2026-10-03T00:00:00+00:00",
    }


@pytest.mark.parametrize(
    ("user", "source", "grants", "has_grant", "access_allowed", "usable_now", "reasons", "keys"),
    [
        (_user(), _source(), [], False, False, False, ("NO_GRANT",), ()),
        (_user(), _source(), [_direct()], True, True, True, (), ("direct",)),
        (
            _user(),
            _source(),
            [_role("role-z", "Zeta"), _role("role-a", "Alpha")],
            True,
            True,
            True,
            (),
            ("role:role-a", "role:role-z"),
        ),
        (
            _user(),
            _source(),
            [_role("role-z", "Zeta"), _direct(), _role("role-a", "Alpha")],
            True,
            True,
            True,
            (),
            ("direct", "role:role-a", "role:role-z"),
        ),
        (
            _user(role="admin"),
            _source(),
            [_direct(), _role("role-a", "Alpha")],
            True,
            True,
            True,
            (),
            ("admin_identity",),
        ),
        (
            _user(active=False),
            _source(),
            [_direct()],
            True,
            False,
            False,
            ("ACCOUNT_DISABLED",),
            ("direct",),
        ),
        (
            _user(),
            _source(enabled=False, runtime_status="disabled"),
            [_direct()],
            True,
            False,
            False,
            ("SOURCE_DISABLED", "SOURCE_NOT_READY"),
            ("direct",),
        ),
        (
            _user(),
            _source(runtime_status="unavailable"),
            [_direct()],
            True,
            True,
            False,
            ("SOURCE_NOT_READY",),
            ("direct",),
        ),
        (
            _user(),
            _source(exists=False, runtime_status="missing"),
            [_direct()],
            True,
            False,
            False,
            ("SOURCE_MISSING",),
            ("direct",),
        ),
    ],
)
def test_explains_all_grant_paths_and_blocking_states(
    user: dict[str, object],
    source: dict[str, object],
    grants: list[dict[str, object]],
    has_grant: bool,
    access_allowed: bool,
    usable_now: bool,
    reasons: tuple[str, ...],
    keys: tuple[str, ...],
) -> None:
    permission = explain_permission(user, source, grants)

    assert permission.has_grant is has_grant
    assert permission.access_allowed is access_allowed
    assert permission.runtime_ready is (source["runtime_status"] == "ready" and source["exists"])
    assert permission.usable_now is usable_now
    assert permission.block_reasons == reasons
    assert tuple(item.key for item in permission.grant_sources) == keys


def test_preview_removal_reports_remaining_access_sources() -> None:
    permission = explain_permission(_user(), _source(), [_direct(), _role("role-a", "Alpha")])

    impact = preview_source_removal(permission, "direct")

    assert impact.remaining_sources == ("role:role-a",)
    assert impact.access_allowed_after is True
    assert impact.would_lose_access is False


def test_preview_removing_last_source_reports_access_loss() -> None:
    permission = explain_permission(_user(), _source(), [_role("role-a", "Alpha")])

    impact = preview_source_removal(permission, "role:role-a")

    assert impact.remaining_sources == ()
    assert impact.access_allowed_after is False
    assert impact.would_lose_access is True


def test_admin_identity_cannot_be_removed_as_a_grant_source() -> None:
    permission = explain_permission(_user(role="admin"), _source(), [])

    with pytest.raises(ValueError, match="administrator identity"):
        preview_source_removal(permission, "admin_identity")


def test_preview_rejects_a_source_not_in_the_current_permission() -> None:
    permission = explain_permission(_user(), _source(), [_direct()])

    with pytest.raises(ValueError, match="not present"):
        preview_source_removal(permission, "role:role-missing")
