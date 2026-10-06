from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator


class SerializedWrenToolkit:
    """Serialize operations that may touch a cached Wren connector."""

    def __init__(self, toolkit: Any) -> None:
        self._toolkit = toolkit
        self.operation_lock = RLock()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._toolkit, name)

    @contextmanager
    def operation(self) -> Iterator[None]:
        with self.operation_lock:
            yield

    def dry_plan(self, sql: str) -> str:
        with self.operation_lock:
            return self._toolkit.dry_plan(sql)

    def dry_run(self, sql: str) -> None:
        with self.operation_lock:
            self._toolkit.dry_run(sql)

    def query(self, sql: str, limit: int | None = None) -> Any:
        with self.operation_lock:
            return self._toolkit.query(sql, limit=limit)


@contextmanager
def serialized_wren_operation(toolkit: Any) -> Iterator[None]:
    """Use the toolkit lock, while keeping lightweight test doubles usable."""
    operation = getattr(toolkit, "operation", None)
    if callable(operation):
        with operation():
            yield
        return
    yield


def build_wren_toolkit(
    project_dir: Path,
    profile_name: str,
    *,
    wren_home: Path | None = None,
) -> Any:
    """Bind a toolkit to one server-selected Wren project and profile."""
    project_dir = project_dir.expanduser().resolve()
    if wren_home is not None:
        os.environ["WREN_HOME"] = str(wren_home.expanduser().resolve())
    project_file = project_dir / "wren_project.yml"
    mdl_file = project_dir / "target" / "mdl.json"
    if not project_file.is_file() or not mdl_file.is_file():
        raise ValueError(
            "Wren project is not ready: expected wren_project.yml and target/mdl.json. "
            "Run `wren context validate` and `wren context build` first."
        )
    # Wren snapshots its WREN_HOME at import time, so set it before importing
    # the toolkit package.
    from wren_langchain import WrenToolkit  # noqa: PLC0415

    return SerializedWrenToolkit(
        WrenToolkit.from_project(
            project_dir,
            profile=profile_name,
        )
    )
