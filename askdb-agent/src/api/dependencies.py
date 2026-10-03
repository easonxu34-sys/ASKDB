from __future__ import annotations

import threading

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from application.auth import AuthApplication
from domain.auth import (
    AccountNotFound,
    AdminPrivilegeRequired,
    AuthError,
    AuthStoreUnavailable,
    BootstrapAlreadyInitialized,
    DataSourceGrantError,
    InvalidCredentials,
    InvalidSession,
    LastActiveAdmin,
    LoginRateLimited,
    PasswordPolicyError,
    Principal,
    UsernameConflict,
)


bearer_scheme = HTTPBearer(auto_error=False)


def get_auth_application(request: Request) -> AuthApplication:
    application: AuthApplication = request.app.state.auth_application
    if not request.app.state.auth_store_ready:
        lock: threading.Lock = request.app.state.auth_store_lock
        with lock:
            if not request.app.state.auth_store_ready:
                try:
                    application.store.initialize()
                    request.app.state.auth_store_ready = True
                except AuthStoreUnavailable:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail={
                            "code": "AUTH_STORE_UNAVAILABLE",
                            "message": "账号服务当前不可用，请稍后重试。",
                        },
                        headers={"Cache-Control": "no-store"},
                    ) from None
    return application


def auth_http_exception(error: AuthError) -> HTTPException:
    if isinstance(error, InvalidCredentials):
        status_code = status.HTTP_401_UNAUTHORIZED
        message = str(error)
    elif isinstance(error, InvalidSession):
        status_code = status.HTTP_401_UNAUTHORIZED
        message = str(error)
    elif isinstance(error, AdminPrivilegeRequired):
        status_code = status.HTTP_403_FORBIDDEN
        message = "此操作仅限管理员。"
    elif isinstance(error, LoginRateLimited):
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
        message = str(error)
    elif isinstance(error, PasswordPolicyError):
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        message = str(error)
    elif isinstance(error, UsernameConflict):
        status_code = status.HTTP_409_CONFLICT
        message = str(error)
    elif isinstance(error, (AccountNotFound, DataSourceGrantError)):
        status_code = status.HTTP_404_NOT_FOUND
        message = str(error)
    elif isinstance(error, (LastActiveAdmin, BootstrapAlreadyInitialized)):
        status_code = status.HTTP_409_CONFLICT
        message = str(error)
    elif isinstance(error, AuthStoreUnavailable):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        message = "账号服务当前不可用，请稍后重试。"
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        message = "账号服务当前不可用，请稍后重试。"
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": message},
        headers={"Cache-Control": "no-store"},
    )


def get_authenticated_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    application: AuthApplication = Depends(get_auth_application),
) -> Principal:
    token = credentials.credentials if credentials else None
    try:
        principal = application.authenticate(token)
    except AuthError as exc:
        raise auth_http_exception(exc) from None
    request.state.auth_token = token
    return principal


def require_current_user(
    principal: Principal = Depends(get_authenticated_principal),
) -> Principal:
    if principal.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PASSWORD_CHANGE_REQUIRED",
                "message": "请先修改临时密码。",
            },
            headers={"Cache-Control": "no-store"},
        )
    return principal


def require_admin(
    principal: Principal = Depends(require_current_user),
) -> Principal:
    if principal.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ADMIN_REQUIRED", "message": "此操作仅限管理员。"},
            headers={"Cache-Control": "no-store"},
        )
    return principal


def current_token(request: Request) -> str | None:
    return getattr(request.state, "auth_token", None)
