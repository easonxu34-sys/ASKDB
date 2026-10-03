from __future__ import annotations

import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv


class ModelSettingsUnavailable(RuntimeError):
    """Settings cannot safely be read or changed with the current deployment key."""


class ModelConfigurationError(ValueError):
    """A submitted model configuration is invalid or cannot be reached."""


class ModelProfileNotFound(ModelConfigurationError):
    """The selected server-side model profile does not exist."""


@dataclass(frozen=True)
class ModelConfiguration:
    provider: str
    model: str
    base_url: str
    api_key: str
    id: str = ""
    name: str = ""
    updated_at: str = ""
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tokenizer_id: str | None = None

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_key_configured": bool(self.api_key),
            "available": bool(self.api_key),
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "tokenizer_id": self.tokenizer_id,
        }


def validate_configuration(
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    *,
    context_window_tokens: int | None = None,
    max_output_tokens: int | None = None,
    tokenizer_id: str | None = None,
) -> ModelConfiguration:
    provider = provider.strip().lower()
    model = model.strip()
    base_url = base_url.strip()
    if provider not in {"openai", "deepseek", "custom"}:
        raise ModelConfigurationError("模型供应商不受支持。")
    if not model or len(model) > 200:
        raise ModelConfigurationError("模型名称不能为空且不能超过 200 个字符。")
    try:
        parsed = urlparse(base_url)
        hostname = parsed.hostname
        parsed.port
    except ValueError as exc:
        raise ModelConfigurationError("API 地址必须是有效的 HTTP(S) 绝对地址。") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ModelConfigurationError("API 地址必须是有效的 HTTP(S) 绝对地址。")
    budget_values = (context_window_tokens, max_output_tokens, tokenizer_id)
    if any(value is not None for value in budget_values):
        if any(value is None for value in budget_values):
            raise ModelConfigurationError("记忆上下文预算必须同时配置窗口、输出预留和 tokenizer。")
        if not 1024 <= context_window_tokens <= 2_000_000:
            raise ModelConfigurationError("上下文窗口必须介于 1024 和 2000000 token。")
        if not 1 <= max_output_tokens < context_window_tokens:
            raise ModelConfigurationError("输出预留必须大于 0 且小于上下文窗口。")
        if tokenizer_id not in {"tiktoken:cl100k_base", "tiktoken:o200k_base"}:
            raise ModelConfigurationError("tokenizer 必须选择已支持的显式编码。")
    return ModelConfiguration(
        provider=provider,
        model=model,
        base_url=base_url.rstrip("/"),
        api_key=api_key.strip(),
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        tokenizer_id=tokenizer_id,
    )


class ModelSettingsStore:
    """Encrypted, deployment-wide catalog of OpenAI-compatible model profiles."""

    def __init__(self, database_path: Path | None = None, encryption_key: str | None = None):
        load_dotenv()
        configured_path = database_path or Path(
            os.environ.get(
                "ASKDB_SETTINGS_DB_PATH",
                str(Path(__file__).resolve().parents[1] / "data" / "model-settings.sqlite3"),
            )
        )
        self.database_path = configured_path.expanduser().resolve()
        self._encryption_key = encryption_key or os.environ.get(
            "ASKDB_SETTINGS_ENCRYPTION_KEY", ""
        ).strip()

    def _cipher(self) -> Fernet:
        if not self._encryption_key:
            raise ModelSettingsUnavailable("模型设置加密密钥未配置。")
        try:
            return Fernet(self._encryption_key.encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ModelSettingsUnavailable("模型设置加密密钥格式无效。") from exc

    @property
    def has_encryption_key(self) -> bool:
        return bool(self._encryption_key)

    def _connect(self) -> sqlite3.Connection:
        try:
            self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            connection = sqlite3.connect(self.database_path, timeout=5)
            os.chmod(self.database_path, 0o600)
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """CREATE TABLE IF NOT EXISTS model_profiles (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    base_url TEXT NOT NULL,
                    api_key_ciphertext BLOB,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS model_settings_meta (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    default_profile_id TEXT,
                    FOREIGN KEY(default_profile_id) REFERENCES model_profiles(id)
                )"""
            )
            profile_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(model_profiles)")
            }
            for name, declaration in (
                ("context_window_tokens", "INTEGER"),
                ("max_output_tokens", "INTEGER"),
                ("tokenizer_id", "TEXT"),
            ):
                if name not in profile_columns:
                    connection.execute(
                        f"ALTER TABLE model_profiles ADD COLUMN {name} {declaration}"
                    )
            self._migrate_legacy_row(connection)
            connection.commit()
            return connection
        except (OSError, sqlite3.Error) as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (name,)
        ).fetchone() is not None

    def _migrate_legacy_row(self, connection: sqlite3.Connection) -> None:
        if not self._table_exists(connection, "model_settings"):
            return
        if connection.execute("SELECT 1 FROM model_profiles LIMIT 1").fetchone():
            return
        row = connection.execute(
            "SELECT provider, model, base_url, api_key_ciphertext, updated_at "
            "FROM model_settings WHERE id = 1"
        ).fetchone()
        if row is None:
            return
        profile_id = f"profile_{uuid.uuid4().hex}"
        updated_at = row[4] or datetime.now(UTC).isoformat()
        connection.execute(
            """INSERT INTO model_profiles
               (id, name, provider, model, base_url, api_key_ciphertext, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (profile_id, "默认模型", row[0], row[1], row[2], row[3], updated_at, updated_at),
        )
        connection.execute(
            """INSERT INTO model_settings_meta (id, default_profile_id) VALUES (1, ?)
               ON CONFLICT(id) DO UPDATE SET default_profile_id=excluded.default_profile_id""",
            (profile_id,),
        )

    def _ensure_seeded(self) -> None:
        self._cipher()
        with self._connect() as connection:
            has_profile = connection.execute(
                "SELECT 1 FROM model_profiles LIMIT 1"
            ).fetchone() is not None
        if has_profile:
            return
        configuration = self._environment_configuration()
        self.create(configuration, "默认模型", make_default=True, allow_unavailable=True)

    def _environment_configuration(self) -> ModelConfiguration:
        model = os.environ.get("ASKDB_MODEL", "openai:deepseek-v4-flash").strip()
        base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").strip()
        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        host = (urlparse(base_url).hostname or "").lower()
        provider = (
            "deepseek" if host in {"api.deepseek.com", "api.deepseek.com.cn"}
            else "openai" if host in {"api.openai.com", "api.openai.com.cn"}
            else "custom"
        )
        return validate_configuration(provider, model.removeprefix("openai:"), base_url, api_key)

    def _decode_row(self, row: tuple[object, ...]) -> ModelConfiguration:
        cipher = self._cipher()
        encrypted_key = row[5]
        try:
            api_key = cipher.decrypt(encrypted_key).decode("utf-8") if encrypted_key else ""
        except (InvalidToken, UnicodeDecodeError, TypeError, ValueError) as exc:
            raise ModelSettingsUnavailable("模型设置无法解密，请检查部署加密密钥。") from exc
        return ModelConfiguration(
            provider=str(row[2]),
            model=str(row[3]),
            base_url=str(row[4]),
            api_key=api_key,
            id=str(row[0]),
            name=str(row[1]),
            updated_at=str(row[7]),
            context_window_tokens=(int(row[8]) if row[8] is not None else None),
            max_output_tokens=(int(row[9]) if row[9] is not None else None),
            tokenizer_id=(str(row[10]) if row[10] is not None else None),
        )

    def list_profiles(self, *, seed_if_missing: bool = True) -> tuple[str | None, list[ModelConfiguration]]:
        if seed_if_missing:
            self._ensure_seeded()
        else:
            self._cipher()
        try:
            with self._connect() as connection:
                default = connection.execute(
                    "SELECT default_profile_id FROM model_settings_meta WHERE id = 1"
                ).fetchone()
                rows = connection.execute(
                    """SELECT id, name, provider, model, base_url, api_key_ciphertext,
                              created_at, updated_at, context_window_tokens,
                              max_output_tokens, tokenizer_id
                       FROM model_profiles ORDER BY created_at, id"""
                ).fetchall()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        return (str(default[0]) if default and default[0] else None, [self._decode_row(row) for row in rows])

    def get_profile(self, profile_id: str) -> ModelConfiguration:
        self._ensure_seeded()
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """SELECT id, name, provider, model, base_url, api_key_ciphertext,
                              created_at, updated_at, context_window_tokens,
                              max_output_tokens, tokenizer_id
                       FROM model_profiles WHERE id = ?""",
                    (profile_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        if row is None:
            raise ModelProfileNotFound("模型配置不存在。")
        return self._decode_row(row)

    def get_default(self, *, seed_if_missing: bool = True) -> ModelConfiguration:
        default_id, profiles = self.list_profiles(seed_if_missing=seed_if_missing)
        if default_id:
            for profile in profiles:
                if profile.id == default_id:
                    return profile
        if profiles:
            return profiles[0]
        if not self.has_encryption_key:
            return self._environment_configuration()
        raise ModelSettingsUnavailable("默认模型配置不可用。")

    def has_saved_settings(self) -> bool:
        if not self.database_path.exists():
            return False
        try:
            with sqlite3.connect(self.database_path, timeout=2) as connection:
                if self._table_exists(connection, "model_profiles") and connection.execute(
                    "SELECT 1 FROM model_profiles LIMIT 1"
                ).fetchone():
                    return True
                if self._table_exists(connection, "model_settings") and connection.execute(
                    "SELECT 1 FROM model_settings WHERE id = 1"
                ).fetchone():
                    return True
                return False
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc

    def ensure_available(self) -> None:
        self._cipher()
        if self.has_saved_settings():
            self.list_profiles(seed_if_missing=False)

    def create(
        self,
        configuration: ModelConfiguration,
        name: str,
        *,
        make_default: bool = False,
        allow_unavailable: bool = False,
    ) -> ModelConfiguration:
        self._cipher()
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 100:
            raise ModelConfigurationError("配置名称不能为空且不能超过 100 个字符。")
        if not configuration.api_key and not allow_unavailable:
            raise ModelConfigurationError("新增模型配置必须提供 API Key。")
        profile_id = f"profile_{uuid.uuid4().hex}"
        now = datetime.now(UTC).isoformat()
        ciphertext = (
            self._cipher().encrypt(configuration.api_key.encode("utf-8"))
            if configuration.api_key else None
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO model_profiles
                       (id, name, provider, model, base_url, api_key_ciphertext, created_at,
                        updated_at, context_window_tokens, max_output_tokens, tokenizer_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (profile_id, clean_name, configuration.provider, configuration.model,
                     configuration.base_url, ciphertext, now, now,
                     configuration.context_window_tokens, configuration.max_output_tokens,
                     configuration.tokenizer_id),
                )
                default = connection.execute(
                    "SELECT default_profile_id FROM model_settings_meta WHERE id = 1"
                ).fetchone()
                if make_default or not default or not default[0]:
                    connection.execute(
                        """INSERT INTO model_settings_meta (id, default_profile_id) VALUES (1, ?)
                           ON CONFLICT(id) DO UPDATE SET default_profile_id=excluded.default_profile_id""",
                        (profile_id,),
                    )
                connection.commit()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        return ModelConfiguration(
            configuration.provider, configuration.model, configuration.base_url,
            configuration.api_key, profile_id, clean_name, now,
            configuration.context_window_tokens, configuration.max_output_tokens,
            configuration.tokenizer_id,
        )

    def update(
        self, profile_id: str, configuration: ModelConfiguration, name: str
    ) -> ModelConfiguration:
        self._cipher()
        existing = self.get_profile(profile_id)
        clean_name = name.strip()
        if not clean_name or len(clean_name) > 100:
            raise ModelConfigurationError("配置名称不能为空且不能超过 100 个字符。")
        api_key = configuration.api_key or existing.api_key
        if not api_key:
            raise ModelConfigurationError("模型配置缺少 API Key。")
        now = datetime.now(UTC).isoformat()
        ciphertext = self._cipher().encrypt(api_key.encode("utf-8"))
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """UPDATE model_profiles SET name=?, provider=?, model=?, base_url=?,
                              api_key_ciphertext=?, updated_at=?, context_window_tokens=?,
                              max_output_tokens=?, tokenizer_id=? WHERE id=?""",
                    (clean_name, configuration.provider, configuration.model,
                     configuration.base_url, ciphertext, now,
                     configuration.context_window_tokens, configuration.max_output_tokens,
                     configuration.tokenizer_id, profile_id),
                )
                if cursor.rowcount == 0:
                    raise ModelConfigurationError("模型配置不存在。")
                connection.commit()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        return ModelConfiguration(
            configuration.provider, configuration.model, configuration.base_url,
            api_key, profile_id, clean_name, now,
            configuration.context_window_tokens, configuration.max_output_tokens,
            configuration.tokenizer_id,
        )

    def set_default(self, profile_id: str) -> str:
        profile = self.get_profile(profile_id)
        if not profile.api_key:
            raise ModelConfigurationError("不可将未配置 API Key 的模型设为默认。")
        try:
            with self._connect() as connection:
                connection.execute(
                    """INSERT INTO model_settings_meta (id, default_profile_id) VALUES (1, ?)
                       ON CONFLICT(id) DO UPDATE SET default_profile_id=excluded.default_profile_id""",
                    (profile_id,),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        return profile_id

    def delete(self, profile_id: str, new_default_id: str | None = None) -> str | None:
        self._cipher()
        try:
            with self._connect() as connection:
                current = connection.execute(
                    "SELECT default_profile_id FROM model_settings_meta WHERE id = 1"
                ).fetchone()
                if not connection.execute(
                    "SELECT 1 FROM model_profiles WHERE id = ?", (profile_id,)
                ).fetchone():
                    raise ModelProfileNotFound("模型配置不存在。")
                default_id = str(current[0]) if current and current[0] else None
                if default_id == profile_id:
                    candidate_row = connection.execute(
                        """SELECT id, name, provider, model, base_url, api_key_ciphertext,
                                  created_at, updated_at, context_window_tokens,
                                  max_output_tokens, tokenizer_id
                           FROM model_profiles WHERE id=? AND id<>? AND api_key_ciphertext IS NOT NULL""",
                        (new_default_id, profile_id),
                    ).fetchone() if new_default_id else None
                    if not candidate_row or not self._decode_row(candidate_row).api_key:
                        raise ModelConfigurationError("删除默认模型时必须指定另一项可用配置作为新默认。")
                    default_id = new_default_id
                    connection.execute(
                        "UPDATE model_settings_meta SET default_profile_id=? WHERE id=1",
                        (default_id,),
                    )
                connection.execute("DELETE FROM model_profiles WHERE id=?", (profile_id,))
                connection.commit()
                return default_id
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc

    def clear_credential(self, profile_id: str) -> ModelConfiguration:
        configuration = self.get_profile(profile_id)
        try:
            with self._connect() as connection:
                connection.execute(
                    "UPDATE model_profiles SET api_key_ciphertext=NULL, updated_at=? WHERE id=?",
                    (datetime.now(UTC).isoformat(), profile_id),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise ModelSettingsUnavailable("模型设置存储当前不可用。") from exc
        return ModelConfiguration(
            configuration.provider, configuration.model, configuration.base_url, "",
            configuration.id, configuration.name, datetime.now(UTC).isoformat(),
            configuration.context_window_tokens, configuration.max_output_tokens,
            configuration.tokenizer_id,
        )

    # Backward-compatible helpers for the singular settings API.
    def get(self, *, seed_if_missing: bool = True) -> ModelConfiguration:
        return self.get_default(seed_if_missing=seed_if_missing)

    def save(self, configuration: ModelConfiguration) -> None:
        default_id, profiles = self.list_profiles()
        if default_id:
            self.update(default_id, configuration, next(
                (profile.name for profile in profiles if profile.id == default_id), "默认模型"
            ))
        else:
            self.create(configuration, "默认模型", make_default=True)
