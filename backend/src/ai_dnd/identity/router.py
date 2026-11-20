"""Роутер identity: /api/auth (регистрация, вход, OAuth, CSRF), /api/me, /api/admin."""

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    FastAPI,
    HTTPException,
    Request,
    Response,
    status,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.sessions import SessionMiddleware

from ai_dnd.admin import service as admin_service
from ai_dnd.billing.ledger import CreditLedger
from ai_dnd.config import Environment
from ai_dnd.identity.csrf import enforce_csrf, issue_csrf_token
from ai_dnd.identity.deps import (
    get_current_user,
    get_mailer_dep,
    get_oauth_dep,
    get_settings_dep,
    require_role,
)
from ai_dnd.identity.mailer import Mailer, build_mailer
from ai_dnd.identity.models import OauthAccountRow, UserRow
from ai_dnd.identity.oauth import PROVIDERS, OAuth, build_oauth, fetch_userinfo, get_client
from ai_dnd.identity.passwords import hash_password, verify_password
from ai_dnd.identity.schemas import (
    CsrfResponse,
    LoginRequest,
    MeResponse,
    PatchMeRequest,
    RegisterRequest,
    RoleUpdateRequest,
    StatusResponse,
    UiSettings,
    VerifyEmailRequest,
)
from ai_dnd.identity.sessions import (
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    revoke_session,
    set_session_cookie,
)
from ai_dnd.identity.settings import IdentitySettings, get_identity_settings
from ai_dnd.identity.tokens import TokenError, make_email_token, read_email_token
from ai_dnd.resources import Resources, get_resources

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["identity"], dependencies=[Depends(enforce_csrf)])

SettingsDep = Annotated[IdentitySettings, Depends(get_settings_dep)]
ResourcesDep = Annotated[Resources, Depends(get_resources)]
MailerDep = Annotated[Mailer, Depends(get_mailer_dep)]
OAuthDep = Annotated[OAuth, Depends(get_oauth_dep)]
CurrentUser = Annotated[UserRow, Depends(get_current_user)]


def _me(user: UserRow) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        locale=user.locale,
        email_verified=user.email_verified_at is not None,
        ui_settings=UiSettings.model_validate(user.ui_settings or {}),
    )


@router.get("/api/auth/csrf")
async def get_csrf(request: Request, response: Response, settings: SettingsDep) -> CsrfResponse:
    return CsrfResponse(token=issue_csrf_token(request, response, settings))


@router.post("/api/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    background_tasks: BackgroundTasks,
    resources: ResourcesDep,
    settings: SettingsDep,
    mailer: MailerDep,
) -> MeResponse:
    email = payload.email.lower()
    password_hash = await asyncio.to_thread(hash_password, payload.password)
    demo = settings.demo_auth or settings.environment == Environment.DEV
    async with resources.db_sessions() as db:
        user = UserRow(email=email, display_name=payload.display_name, password_hash=password_hash)
        if demo:
            user.email_verified_at = datetime.now(UTC)
        db.add(user)
        try:
            await db.commit()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail={"code": "email_taken"}
            ) from exc
        token = make_email_token(settings, user.id, email)
    # Решение пользователя 18.11: стартовые кредиты сразу, баланс новичка не нулевой.
    await CreditLedger(resources.db_sessions).grant_daily_free(user.id)
    if not demo:
        link = f"{settings.base_url.rstrip('/')}/verify-email?token={token}"
        background_tasks.add_task(mailer.send_confirmation, email, link)
    return _me(user)


@router.post("/api/auth/verify-email")
async def verify_email(
    payload: VerifyEmailRequest, resources: ResourcesDep, settings: SettingsDep
) -> StatusResponse:
    try:
        user_id, email = read_email_token(settings, payload.token)
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail={"code": exc.code}
        ) from exc
    async with resources.db_sessions() as db:
        user = await db.get(UserRow, user_id)
        if user is None or user.email != email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_token"}
            )
        if user.email_verified_at is None:
            user.email_verified_at = datetime.now(UTC)
            await db.commit()
    return StatusResponse(status="verified")


@router.post("/api/auth/login")
async def login(
    payload: LoginRequest,
    response: Response,
    resources: ResourcesDep,
    settings: SettingsDep,
) -> MeResponse:
    async with resources.db_sessions() as db:
        stmt = select(UserRow).where(UserRow.email == payload.email.lower())
        user = (await db.execute(stmt)).scalar_one_or_none()
        password_ok = (
            user is not None
            and user.password_hash is not None
            and (await asyncio.to_thread(verify_password, user.password_hash, payload.password))
        )
        if user is None or not password_ok:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_credentials"}
            )
        if user.banned_at is not None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="banned")
        demo = settings.demo_auth or settings.environment == Environment.DEV
        if not demo and user.email_verified_at is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail={"code": "email_not_verified"}
            )
        _, token = await create_session(db, user.id, settings.session_ttl_days)
        await db.commit()
    set_session_cookie(response, token, settings)
    return _me(user)


@router.post("/api/auth/logout")
async def logout(
    request: Request,
    response: Response,
    user: CurrentUser,
    resources: ResourcesDep,
    settings: SettingsDep,
) -> StatusResponse:
    token = request.cookies.get(SESSION_COOKIE, "")
    async with resources.db_sessions() as db:
        await revoke_session(db, token)
        await db.commit()
    clear_session_cookie(response, settings)
    return StatusResponse(status="ok")


@router.get("/api/me")
async def get_me(user: CurrentUser) -> MeResponse:
    return _me(user)


@router.patch("/api/me")
async def patch_me(
    payload: PatchMeRequest, user: CurrentUser, resources: ResourcesDep
) -> MeResponse:
    async with resources.db_sessions() as db:
        row = await db.get(UserRow, user.id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail={"code": "not_authenticated"}
            )
        if payload.display_name is not None:
            row.display_name = payload.display_name
        if payload.locale is not None:
            row.locale = payload.locale
        if payload.ui_settings is not None:
            row.ui_settings = payload.ui_settings.model_dump()
        await db.commit()
        return _me(row)


@router.patch("/api/admin/users/{user_id}/role")
async def set_user_role(
    user_id: uuid.UUID,
    payload: RoleUpdateRequest,
    admin: Annotated[UserRow, Depends(require_role("admin"))],
    resources: ResourcesDep,
) -> MeResponse:
    # Единая логика с POST /api/admin/users/{id}/role: self-check, аудит (admin.service).
    target = await admin_service.change_role(resources.db_sessions, admin, user_id, payload.role)
    logger.info("role_changed", target=str(target.id), role=payload.role, by=str(admin.id))
    return _me(target)


def _oauth_client(oauth: OAuth, provider: str) -> Any:
    if get_client(oauth, provider) is None:
        if provider not in PROVIDERS:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail={"code": "unknown_provider"}
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "oauth_not_configured",
                "params": {"provider": provider},
                "message": f"Провайдер {provider} не настроен: задайте ключи в .env",
            },
        )
    return oauth.create_client(provider)


@router.get("/api/auth/oauth/{provider}/login")
async def oauth_login(
    provider: str, request: Request, settings: SettingsDep, oauth: OAuthDep
) -> Response:
    client = _oauth_client(oauth, provider)
    redirect_uri = f"{settings.base_url.rstrip('/')}/api/auth/oauth/{provider}/callback"
    response: Response = await client.authorize_redirect(request, redirect_uri)
    return response


@router.get("/api/auth/oauth/{provider}/callback")
async def oauth_callback(
    provider: str, request: Request, resources: ResourcesDep, settings: SettingsDep, oauth: OAuthDep
) -> Response:
    _oauth_client(oauth, provider)
    userinfo = await fetch_userinfo(oauth, provider, request)
    account_id = str(userinfo.get("sub") or userinfo.get("id") or "")
    if not account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail={"code": "oauth_no_account_id"}
        )
    raw_email = userinfo.get("email")
    email = str(raw_email).lower() if raw_email else None
    display_name = str(
        userinfo.get("name") or userinfo.get("username") or userinfo.get("global_name") or "Игрок"
    )[:80]
    async with resources.db_sessions() as db:
        stmt = select(OauthAccountRow).where(
            OauthAccountRow.provider == provider,
            OauthAccountRow.provider_account_id == account_id,
        )
        account = (await db.execute(stmt)).scalar_one_or_none()
        user: UserRow | None = None
        if account is not None:
            user = await db.get(UserRow, account.user_id)
        if user is None:
            if email:
                stmt_user = select(UserRow).where(UserRow.email == email)
                existing = (await db.execute(stmt_user)).scalar_one_or_none()
                # Привязываем к существующему аккаунту только с подтверждённым email провайдера.
                if existing is not None and userinfo.get("email_verified"):
                    user = existing
            if user is None:
                taken_stmt = select(UserRow.id).where(UserRow.email == email)
                email_taken = email is not None and (
                    (await db.execute(taken_stmt)).first() is not None
                )
                user = UserRow(
                    email=None if email_taken else email,
                    display_name=display_name,
                    email_verified_at=datetime.now(UTC) if email and not email_taken else None,
                )
                db.add(user)
                await db.flush()
            if account is None:
                db.add(
                    OauthAccountRow(
                        user_id=user.id, provider=provider, provider_account_id=account_id
                    )
                )
        _, token = await create_session(db, user.id, settings.session_ttl_days)
        await db.commit()
    await CreditLedger(resources.db_sessions).grant_daily_free(user.id)
    response = RedirectResponse(settings.base_url.rstrip("/") + "/", status_code=302)
    set_session_cookie(response, token, settings)
    return response


def install_identity(
    app: FastAPI,
    settings: IdentitySettings | None = None,
    mailer: Mailer | None = None,
) -> None:
    """Подключает identity к приложению: SessionMiddleware (для OAuth state) и роутер."""
    settings = settings if settings is not None else get_identity_settings()
    app.state.identity_settings = settings
    app.state.identity_mailer = mailer if mailer is not None else build_mailer(settings)
    app.state.identity_oauth = build_oauth(settings)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key.get_secret_value(),
        same_site="lax",
        https_only=settings.environment != Environment.DEV,
    )
    app.include_router(router)
