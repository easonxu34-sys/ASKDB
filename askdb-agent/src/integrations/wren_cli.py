from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

import yaml

from wren_settings import WrenConfigurationError


_PROFILE_TOKEN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")
_SENSITIVE_PROFILE_FIELDS = {
    "password", "passwd", "sslca", "ssl_ca", "ca", "ca_pem", "client_key",
    "private_key", "privatekey", "credentials", "accesstoken", "access_token",
    "clientsecret", "client_secret", "access_key_id", "accesskeyid",
    "access_key_secret", "aws_access_key_id", "aws_secret_access_key",
    "aws_session_token", "web_identity_token",
    "dsn", "secret", "token",
}


class WrenCli:
    """Small, fixed-command boundary around the installed Wren CLI."""

    def __init__(
        self,
        command_runner: Callable[..., Any] = subprocess.run,
        wren_home: Path | None = None,
        *,
        timeout_seconds: int = 120,
    ):
        self.command_runner = command_runner
        self.wren_home = (wren_home or Path.home() / ".wren").expanduser().resolve()
        self.timeout_seconds = timeout_seconds

    def _environment(self, secret_values: dict[str, str] | None = None) -> dict[str, str]:
        environment = os.environ.copy()
        environment["WREN_HOME"] = str(self.wren_home)
        for name, value in (secret_values or {}).items():
            if not re.fullmatch(r"ASKDB_WREN_[A-Z0-9_]+", name):
                raise WrenConfigurationError("Wren 凭证标识无效。")
            environment[name] = value
        return environment

    def _run(
        self,
        args: list[str],
        *,
        cwd: Path,
        secret_values: dict[str, str] | None = None,
        secrets_to_redact: list[str] | None = None,
    ) -> str:
        try:
            result = self.command_runner(
                args,
                cwd=cwd,
                env=self._environment(secret_values),
                shell=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise WrenConfigurationError("Wren 操作超时，请稍后重试。") from exc
        except OSError as exc:
            raise WrenConfigurationError("Wren 命令当前不可用。") from exc
        if result.returncode != 0:
            # Raw CLI diagnostics may contain SQL, endpoints, usernames, or
            # credentials. Keep the stable code and let the UI show guidance.
            if "validate" in args:
                code = "WREN_VALIDATION_FAILED"
            elif "profile" in args:
                code = "WREN_CONNECTION_FAILED"
            else:
                code = "WREN_BUILD_FAILED"
            message = "Wren 操作失败，请检查连接配置和语义模型。"
            raise WrenConfigurationError(message, code=code)
        return result.stdout or ""

    def add_profile(
        self,
        profile_name: str,
        connection_fields: dict[str, Any],
        *,
        secret_values: dict[str, str] | None = None,
        secrets_to_redact: list[str] | None = None,
        sensitive_field_names: set[str] | None = None,
    ) -> str:
        if not _PROFILE_TOKEN.fullmatch(profile_name):
            raise WrenConfigurationError("Wren profile 名称无效。")
        if not isinstance(connection_fields, dict):
            raise WrenConfigurationError("Wren profile 只能包含非敏感连接字段。")
        schema_sensitive_names = {
            name.lower() for name in (sensitive_field_names or set())
        }
        for key, value in connection_fields.items():
            lowered = key.lower()
            sensitive = (
                lowered in _SENSITIVE_PROFILE_FIELDS
                or lowered in schema_sensitive_names
                or (
                    not lowered.endswith("_type")
                    and any(
                        marker in lowered
                        for marker in (
                            "password", "passwd", "secret", "credential",
                            "private_key", "privatekey", "access_key", "accesskey",
                            "client_key", "clientkey", "certificate", "ca_pem", "ssl_ca",
                        )
                    )
                )
                or lowered == "token"
                or lowered.endswith("_token")
                or lowered.endswith("_key")
            )
            if sensitive and not (
                isinstance(value, str)
                and re.fullmatch(r"\$\{ASKDB_WREN_[A-Z0-9_]+\}", value)
            ):
                raise WrenConfigurationError("Wren profile 敏感字段必须使用服务端变量引用。")
        self.wren_home.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self.wren_home, 0o700)
        descriptor, filename = tempfile.mkstemp(prefix="askdb-wren-profile-", suffix=".yml", dir=self.wren_home)
        path = Path(filename)
        try:
            os.chmod(path, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                yaml.safe_dump(connection_fields, stream, allow_unicode=True, sort_keys=False)
            self._run(
                ["wren", "profile", "add", profile_name, "--from-file", str(path), "--no-validate"],
                cwd=self.wren_home,
                secret_values=secret_values,
                secrets_to_redact=secrets_to_redact,
            )
        finally:
            path.unlink(missing_ok=True)
        return profile_name

    def validate(self, project_dir: Path, *, secrets_to_redact: list[str] | None = None) -> str:
        return self._run(
            ["wren", "context", "validate"],
            cwd=project_dir,
            secrets_to_redact=secrets_to_redact,
        )

    def build(self, project_dir: Path, *, secrets_to_redact: list[str] | None = None) -> str:
        return self._run(
            ["wren", "context", "build"],
            cwd=project_dir,
            secrets_to_redact=secrets_to_redact,
        )
