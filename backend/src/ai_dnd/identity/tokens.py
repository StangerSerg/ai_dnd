"""Подписанные токены itsdangerous: подтверждение email и приглашения, с TTL."""

import uuid
from typing import Any, cast

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ai_dnd.identity.settings import IdentitySettings

EMAIL_SALT = "identity.email-confirmation"
INVITE_SALT = "identity.invite"


class TokenError(ValueError):
    """Токен недействителен или истёк; code — машинный код ошибки для API."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _serializer(settings: IdentitySettings) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key.get_secret_value())


def _loads(settings: IdentitySettings, token: str, salt: str, max_age: int) -> dict[str, Any]:
    try:
        data = _serializer(settings).loads(token, salt=salt, max_age=max_age)
    except SignatureExpired as exc:
        raise TokenError("token_expired") from exc
    except BadSignature as exc:
        raise TokenError("invalid_token") from exc
    return cast(dict[str, Any], data)


def make_email_token(settings: IdentitySettings, user_id: uuid.UUID, email: str) -> str:
    return _serializer(settings).dumps({"sub": str(user_id), "email": email}, salt=EMAIL_SALT)


def read_email_token(settings: IdentitySettings, token: str) -> tuple[uuid.UUID, str]:
    data = _loads(settings, token, EMAIL_SALT, settings.email_token_ttl_hours * 3600)
    return uuid.UUID(str(data["sub"])), str(data["email"])


def make_invite_token(settings: IdentitySettings, payload: dict[str, str]) -> str:
    return _serializer(settings).dumps(payload, salt=INVITE_SALT)


def read_invite_token(settings: IdentitySettings, token: str) -> dict[str, str]:
    data = _loads(settings, token, INVITE_SALT, settings.invite_token_ttl_days * 86400)
    return {str(k): str(v) for k, v in data.items()}
