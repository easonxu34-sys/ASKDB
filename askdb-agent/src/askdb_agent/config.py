from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    wren_project_dir: Path | None
    wren_profile: str | None
    model: str
    base_url: str | None = None
    api_key: str | None = None
    wren_home: Path | None = None
    legacy_wren_home: Path | None = None
    wren_data_dir: Path | None = None

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv()
        project_value = os.environ.get("WREN_PROJECT_DIR", "").strip()
        profile = os.environ.get("WREN_PROFILE", "").strip()
        model = os.environ.get("ASKDB_MODEL", "openai:deepseek-v4-flash").strip()
        base_url = os.environ.get("OPENAI_BASE_URL", "").strip() or None
        api_key = os.environ.get("OPENAI_API_KEY", "").strip() or None
        project_dir = Path(project_value).expanduser().resolve() if project_value else None
        if not model:
            raise ValueError("ASKDB_MODEL cannot be empty.")
        wren_home_value = os.environ.get("WREN_HOME", "").strip()
        wren_data_value = os.environ.get("ASKDB_WREN_DATA_DIR", "").strip()
        data_root = Path(__file__).resolve().parents[2] / "data"
        wren_home = Path(wren_home_value).expanduser().resolve() if wren_home_value else data_root / "wren-home"
        legacy_wren_home = Path(wren_home_value).expanduser().resolve() if wren_home_value else Path.home() / ".wren"
        return cls(
            wren_project_dir=project_dir,
            wren_profile=profile or None,
            model=model,
            base_url=base_url,
            api_key=api_key,
            wren_home=wren_home,
            legacy_wren_home=legacy_wren_home,
            wren_data_dir=Path(wren_data_value).expanduser().resolve() if wren_data_value else data_root / "wren",
        )
