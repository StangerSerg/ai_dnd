"""Схемы запросов и ответов identity (Pydantic v2)."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

# email-validator не подключён (разрешены только 4 новые зависимости) — проверяем паттерном.
EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

Role = Literal["user", "moderator", "admin"]
Locale = Literal["ru", "en"]
DiceStyle = Literal["amber", "jade", "amethyst", "gold", "malachite", "ruby"]


class UiSettings(BaseModel):
    dice_style: DiceStyle = "amber"
    less_animation: bool = False


class RegisterRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=8, max_length=128)
    display_name: str = Field(min_length=1, max_length=80)


class LoginRequest(BaseModel):
    email: str = Field(pattern=EMAIL_PATTERN, max_length=255)
    password: str = Field(min_length=1, max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(min_length=1)


class PatchMeRequest(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    locale: Locale | None = None
    ui_settings: UiSettings | None = None


class RoleUpdateRequest(BaseModel):
    role: Role


class CsrfResponse(BaseModel):
    token: str


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str | None
    display_name: str
    role: str
    locale: str
    email_verified: bool
    ui_settings: UiSettings = Field(default_factory=UiSettings)


class StatusResponse(BaseModel):
    status: str
