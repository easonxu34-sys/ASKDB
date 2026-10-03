from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


UserRole = Literal["admin", "member"]


@dataclass(frozen=True)
class Principal:
    user_id: str
    username: str
    role: UserRole
    must_change_password: bool


@dataclass(frozen=True)
class StoredUser:
    id: str
    username: str
    username_key: str
    role: UserRole
    password_hash: str
    is_active: bool
    must_change_password: bool
    created_at: str
    updated_at: str
    last_login_at: str | None

    def principal(self) -> Principal:
        return Principal(
            user_id=self.id,
            username=self.username,
            role=self.role,
            must_change_password=self.must_change_password,
        )


class AuthError(RuntimeError):
    code = "AUTH_ERROR"


class AuthStoreUnavailable(AuthError):
    code = "AUTH_STORE_UNAVAILABLE"


class InvalidCredentials(AuthError):
    code = "INVALID_CREDENTIALS"


class LoginRateLimited(AuthError):
    code = "LOGIN_RATE_LIMITED"


class InvalidSession(AuthError):
    code = "INVALID_SESSION"


class AdminPrivilegeRequired(AuthError):
    code = "ADMIN_REQUIRED"


class PasswordPolicyError(AuthError):
    code = "PASSWORD_POLICY_FAILED"


class UsernameConflict(AuthError):
    code = "USERNAME_ALREADY_EXISTS"


class BootstrapAlreadyInitialized(AuthError):
    code = "INITIAL_ADMIN_ALREADY_EXISTS"


class AccountNotFound(AuthError):
    code = "USER_NOT_FOUND"


class LastActiveAdmin(AuthError):
    code = "LAST_ACTIVE_ADMIN_REQUIRED"


class InvalidAccountChange(AuthError):
    code = "USER_UPDATE_INVALID"


class DataSourceGrantError(AuthError):
    code = "DATA_SOURCE_GRANT_INVALID"
