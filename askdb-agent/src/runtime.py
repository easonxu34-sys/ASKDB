"""Runtime composition kept as a stable import boundary for existing callers."""

from __future__ import annotations

from pathlib import Path

import yaml

from agent.graph import build_graph
from config import Settings
from integrations.models import build_model
from integrations.wren import build_wren_toolkit


def project_dialect(project_dir: Path) -> str:
    manifest_path = project_dir / "wren_project.yml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return "mysql"
    dialect = manifest.get("data_source") if isinstance(manifest, dict) else None
    return str(dialect or "mysql")


def build_runtime(settings: Settings | None = None):
    settings = settings or Settings.from_env()
    if settings.wren_project_dir is None or not settings.wren_profile:
        raise ValueError("没有配置数据源；请在数据源页面完成配置并应用。")
    toolkit = build_wren_toolkit(
        settings.wren_project_dir,
        settings.wren_profile,
        wren_home=settings.legacy_wren_home or settings.wren_home,
    )
    model = build_model(settings)
    return build_graph(
        model=model,
        toolkit=toolkit,
        dialect=project_dialect(settings.wren_project_dir),
    )


__all__ = ["Settings", "build_model", "build_runtime"]
