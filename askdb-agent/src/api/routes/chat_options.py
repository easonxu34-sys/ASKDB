from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response

from api.dependencies import require_current_user
from domain.auth import Principal


router = APIRouter()


@router.get("/v1/chat/model-options")
async def chat_model_options(
    request: Request,
    response: Response,
    _principal: Principal = Depends(require_current_user),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    catalog = await request.app.state.model_settings.public_catalog()
    profiles = catalog.get("profiles")
    safe_profiles = []
    if isinstance(profiles, list):
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            if not all(
                isinstance(profile.get(key), str)
                for key in ("id", "name", "model")
            ) or not isinstance(profile.get("available"), bool):
                continue
            safe_profiles.append(
                {
                    "id": profile["id"],
                    "name": profile["name"],
                    "model": profile["model"],
                    "available": profile["available"],
                }
            )
    default_id = catalog.get("default_profile_id")
    return {
        "default_profile_id": default_id if isinstance(default_id, str) else None,
        "profiles": safe_profiles,
    }
