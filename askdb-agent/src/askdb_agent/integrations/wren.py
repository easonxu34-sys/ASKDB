from __future__ import annotations

import os
from pathlib import Path
from typing import Any


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

    return WrenToolkit.from_project(
        project_dir,
        profile=profile_name,
    )
