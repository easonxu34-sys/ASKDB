#!/usr/bin/env python3
"""Write private launchd plists for the two local public deployment services."""

from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path


def service_plist(
    *,
    label: str,
    program_arguments: list[str],
    working_directory: Path,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, object]:
    return {
        "Label": label,
        "ProgramArguments": program_arguments,
        "WorkingDirectory": str(working_directory),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "StandardOutPath": str(stdout_path),
        "StandardErrorPath": str(stderr_path),
    }


def clean_program_arguments(
    script: Path, environment: dict[str, str], arguments: list[str] | None = None
) -> list[str]:
    values = [f"{name}={value}" for name, value in sorted(environment.items())]
    return ["/usr/bin/env", "-i", *values, "/bin/bash", str(script), *(arguments or [])]


def write_private_plist(path: Path, document: dict[str, object]) -> None:
    with path.open("wb") as stream:
        plistlib.dump(document, stream, fmt=plistlib.FMT_BINARY)
    path.chmod(0o600)


def main() -> int:
    if len(sys.argv) != 6:
        print("usage: write-local-launchd-plists.py APP_ROOT RUN_DIR LOG_DIR ORIGIN NODE_BIN", file=sys.stderr)
        return 2

    app_root = Path(sys.argv[1]).resolve()
    run_dir = Path(sys.argv[2]).resolve()
    log_dir = Path(sys.argv[3]).resolve()
    origin = sys.argv[4]
    node_bin = str(Path(sys.argv[5]).resolve())
    home = str(Path.home().resolve())
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+:8443", origin):
        print("invalid Funnel origin", file=sys.stderr)
        return 2

    deploy_dir = str(app_root.parent)
    system_path = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
    agent_path = f"{app_root.parent / 'venv' / 'bin'}:{system_path}"
    web_path = f"{Path(node_bin).parent}:{system_path}"
    agent_environment = {
        "ASKDB_LOCAL_DEPLOY_DIR": deploy_dir,
        "HOME": home,
        "PATH": agent_path,
    }
    web_environment = {
        "ASKDB_AGENT_URL": "http://127.0.0.1:8001",
        "ASKDB_WEB_ORIGIN": origin,
        "ASKDB_LOCAL_DEPLOY_DIR": deploy_dir,
        "NODE_ENV": "production",
        "HOME": home,
        "PATH": web_path,
    }

    write_private_plist(
        run_dir / "agent.plist",
        service_plist(
            label="com.askdb.local-public.agent",
            program_arguments=clean_program_arguments(
                app_root / "scripts/run-local-agent.sh", agent_environment
            ),
            working_directory=app_root / "askdb-agent",
            stdout_path=log_dir / "agent.stdout.log",
            stderr_path=log_dir / "agent.stderr.log",
        ),
    )
    write_private_plist(
        run_dir / "web.plist",
        service_plist(
            label="com.askdb.local-public.web",
            program_arguments=clean_program_arguments(
                app_root / "scripts/run-local-web.sh", web_environment, [node_bin]
            ),
            working_directory=app_root / "askdb-web",
            stdout_path=log_dir / "web.stdout.log",
            stderr_path=log_dir / "web.stderr.log",
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
