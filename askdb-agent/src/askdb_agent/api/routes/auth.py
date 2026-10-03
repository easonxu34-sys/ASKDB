from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from starlette.concurrency import run_in_threadpool

from askdb_agent.api.dependencies import (
    auth_http_exception,
    current_token,
    get_auth_application,
    get_authenticated_principal,
)
from askdb_agent.api.schemas.auth import ChangePasswordInput, LoginInput
from askdb_agent.application.auth import AuthApplication
from askdb_agent.domain.auth import AuthError, Principal


router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _principal_payload(principal: Principal) -> dict[str, object]:
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role,
        "must_change_password": principal.must_change_password,
    }


@router.post("/login")
async def login(
    body: LoginInput,
    response: Response,
    application: AuthApplication = Depends(get_auth_application),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        token, principal = await run_in_threadpool(
            application.login, body.username, body.password
        )
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    return {"session_token": token, "user": _principal_payload(principal)}


@router.post("/logout")
async def logout(
    request: Request,
    response: Response,
    _principal: Principal = Depends(get_authenticated_principal),
    application: AuthApplication = Depends(get_auth_application),
) -> dict[str, bool]:
    response.headers["Cache-Control"] = "no-store"
    application.logout(current_token(request))
    return {"ok": True}


@router.get("/me")
async def me(
    response: Response,
    principal: Principal = Depends(get_authenticated_principal),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    return _principal_payload(principal)


@router.post("/change-password")
async def change_password(
    body: ChangePasswordInput,
    request: Request,
    response: Response,
    principal: Principal = Depends(get_authenticated_principal),
    application: AuthApplication = Depends(get_auth_application),
) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store"
    try:
        token, updated = await run_in_threadpool(
            application.change_password,
            principal,
            current_token(request),
            body.current_password,
            body.new_password,
        )
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "PASSWORD_POLICY_FAILED", "message": str(exc)},
            headers={"Cache-Control": "no-store"},
        ) from None
    return {"session_token": token, "user": _principal_payload(updated)}
