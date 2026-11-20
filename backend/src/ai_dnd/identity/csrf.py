"""CSRF-защита double-submit: токен в cookie ai_dnd_csrf и заголовке X-CSRF-Token."""

import hmac
import secrets

from fastapi import HTTPException, Request, Response, status

from ai_dnd.config import Environment
from ai_dnd.identity.settings import IdentitySettings

CSRF_COOKIE = "ai_dnd_csrf"
CSRF_HEADER = "X-CSRF-Token"

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def issue_csrf_token(request: Request, response: Response, settings: IdentitySettings) -> str:
    token = request.cookies.get(CSRF_COOKIE) or secrets.token_urlsafe(32)
    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=settings.session_ttl_days * 86400,
        httponly=False,
        samesite="lax",
        secure=settings.environment != Environment.DEV,
        path="/",
    )
    return token


def enforce_csrf(request: Request) -> None:
    """Зависимость роутера: мутирующие запросы обязаны повторить cookie в заголовке."""
    if request.method not in _MUTATING_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not hmac.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "csrf_failed"})
