from __future__ import annotations

import hashlib
import secrets
import unicodedata
import uuid

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from auth_store import AuthStore
from domain.auth import (
    AccountNotFound,
    AuthStoreUnavailable,
    BootstrapAlreadyInitialized,
    InvalidCredentials,
    InvalidSession,
    LastActiveAdmin,
    LoginRateLimited,
    PasswordPolicyError,
    Principal,
    StoredUser,
    UserRole,
)


class AuthApplication:
    def __init__(self, store: AuthStore):
        self.store = store
        self.password_hasher = PasswordHasher(
            time_cost=2,
            memory_cost=19_456,
            parallelism=1,
            type=Type.ID,
        )
        self._dummy_password_hash: str | None = None

    def _dummy_hash(self) -> str:
        if self._dummy_password_hash is None:
            self._dummy_password_hash = self.password_hasher.hash(
                "askdb-invalid-login-timing-padding"
            )
        return self._dummy_password_hash

    @staticmethod
    def normalize_username(username: str) -> tuple[str, str]:
        display = unicodedata.normalize("NFKC", username).strip()
        if not display or len(display) > 128 or any(ord(char) < 32 for char in display):
            raise ValueError("用户名长度或格式无效。")
        return display, display.casefold()

    @staticmethod
    def validate_password(password: str) -> None:
        if len(password) < 8:
            raise PasswordPolicyError("密码至少需要 8 个字符。")
        if len(password) > 1024 or len(password.encode("utf-8")) > 4096:
            raise PasswordPolicyError("密码长度不能超过 1024 个字符。")

    def _hash_password(self, password: str) -> str:
        self.validate_password(password)
        return self.password_hasher.hash(password)

    def _new_temporary_password(self) -> str:
        password = secrets.token_urlsafe(24)
        self.validate_password(password)
        return password

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(32)

    def bootstrap_admin(self, username: str) -> tuple[StoredUser, str]:
        display, key = self.normalize_username(username)
        temporary_password = self._new_temporary_password()
        user = self.store.create_bootstrap_admin(
            user_id=uuid.uuid4().hex,
            username=display,
            username_key=key,
            password_hash=self._hash_password(temporary_password),
        )
        return user, temporary_password

    def recover_admin(self, username: str) -> tuple[StoredUser, str]:
        _, username_key = self.normalize_username(username)
        temporary_password = self._new_temporary_password()
        user = self.store.recover_admin(
            username_key,
            self._hash_password(temporary_password),
        )
        return user, temporary_password

    def login(self, username: str, password: str) -> tuple[str, Principal]:
        try:
            _, username_key = self.normalize_username(username)
        except ValueError:
            username_key = "invalid:" + hashlib.sha256(username.encode("utf-8", "ignore")).hexdigest()
        throttle_key = hashlib.sha256(username_key.encode("utf-8")).hexdigest()
        if self.store.is_login_blocked(throttle_key):
            raise LoginRateLimited("登录失败次数过多，请稍后重试。")

        user = self.store.get_user_for_login(username_key)
        password_hash = user.password_hash if user else self._dummy_hash()
        password_matches = False
        try:
            password_matches = self.password_hasher.verify(password_hash, password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            password_matches = False
        if user is None or not user.is_active or not password_matches:
            self.store.record_login_failure(throttle_key)
            raise InvalidCredentials("用户名或密码错误。")

        self.store.clear_login_failures(throttle_key)
        token = self._new_token()
        self.store.create_session(token_hash=self._token_hash(token), user_id=user.id)
        return token, user.principal()

    def authenticate(self, token: str | None) -> Principal:
        if not token or len(token) > 512:
            raise InvalidSession("请先登录。")
        user = self.store.authenticate_session(self._token_hash(token))
        if user is None:
            raise InvalidSession("登录状态已失效，请重新登录。")
        return user.principal()

    def logout(self, token: str | None) -> None:
        if token:
            self.store.revoke_session(self._token_hash(token))

    def change_password(
        self,
        principal: Principal,
        token: str | None,
        current_password: str,
        new_password: str,
    ) -> tuple[str, Principal]:
        if not token:
            raise InvalidSession("请先登录。")
        user = self.store.get_user(principal.user_id)
        try:
            valid_current = self.password_hasher.verify(user.password_hash, current_password)
        except (VerifyMismatchError, InvalidHashError, VerificationError):
            valid_current = False
        if not valid_current:
            raise InvalidCredentials("当前密码不正确。")
        self.validate_password(new_password)
        try:
            if self.password_hasher.verify(user.password_hash, new_password):
                raise PasswordPolicyError("新密码必须与当前密码不同。")
        except VerifyMismatchError:
            pass
        new_token = self._new_token()
        updated = self.store.change_password(
            user_id=principal.user_id,
            old_token_hash=self._token_hash(token),
            new_token_hash=self._token_hash(new_token),
            password_hash=self.password_hasher.hash(new_password),
        )
        return new_token, updated.principal()

    def create_user(
        self,
        *,
        username: str,
        role: UserRole,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> tuple[StoredUser, str]:
        display, key = self.normalize_username(username)
        temporary_password = self._new_temporary_password()
        user = self.store.create_user(
            user_id=uuid.uuid4().hex,
            username=display,
            username_key=key,
            role=role,
            password_hash=self._hash_password(temporary_password),
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        return user, temporary_password

    def update_user(
        self,
        user_id: str,
        *,
        username: str | None,
        role: UserRole | None,
        is_active: bool | None,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> StoredUser:
        if username is None:
            display = key = None
        else:
            display, key = self.normalize_username(username)
        return self.store.update_user(
            user_id,
            username=display,
            username_key=key,
            role=role,
            is_active=is_active,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def reset_password(
        self,
        user_id: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> str:
        temporary_password = self._new_temporary_password()
        self.store.reset_password(
            user_id,
            self._hash_password(temporary_password),
            actor_user_id=actor_user_id,
            request_id=request_id,
        )
        return temporary_password

    def list_users(self) -> list[dict[str, object]]:
        return [
            {
                "id": user.id,
                "username": user.username,
                "role": user.role,
                "is_active": user.is_active,
                "must_change_password": user.must_change_password,
                "created_at": user.created_at,
                "updated_at": user.updated_at,
                "last_login_at": user.last_login_at,
                "data_source_ids": source_ids,
            }
            for user, source_ids in self.store.list_users()
        ]

    def grant_data_source(
        self,
        user_id: str,
        source_id: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> None:
        self.store.grant_data_source(
            user_id,
            source_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def revoke_data_source(
        self,
        user_id: str,
        source_id: str,
        *,
        actor_user_id: str,
        request_id: str | None = None,
    ) -> None:
        self.store.revoke_data_source(
            user_id,
            source_id,
            actor_user_id=actor_user_id,
            request_id=request_id,
        )

    def source_ids_for(self, principal: Principal) -> set[str] | None:
        if principal.role == "admin":
            return None
        return self.store.granted_data_source_ids(principal.user_id)

    def can_access_data_source(self, principal: Principal, source_id: str) -> bool:
        return self.store.can_access_data_source(principal.user_id, source_id)


__all__ = [
    "AccountNotFound",
    "AuthApplication",
    "AuthStoreUnavailable",
    "BootstrapAlreadyInitialized",
    "InvalidCredentials",
    "InvalidSession",
    "LastActiveAdmin",
    "LoginRateLimited",
    "PasswordPolicyError",
]
