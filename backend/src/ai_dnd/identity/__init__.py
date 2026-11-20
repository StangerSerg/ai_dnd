"""Модуль identity: регистрация, вход, сессии, профили и роли (архитектура, 4 и 13)."""

from ai_dnd.identity.deps import get_current_user, require_role
from ai_dnd.identity.router import install_identity
from ai_dnd.identity.settings import IdentitySettings, get_identity_settings

__all__ = [
    "IdentitySettings",
    "get_current_user",
    "get_identity_settings",
    "install_identity",
    "require_role",
]
