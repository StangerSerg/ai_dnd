"""Отправка писем подтверждения: ConsoleMailer для dev, SmtpMailer при настроенном SMTP."""

from email.message import EmailMessage
from typing import Protocol

import aiosmtplib
import structlog

from ai_dnd.identity.settings import IdentitySettings

logger = structlog.get_logger(__name__)


class Mailer(Protocol):
    async def send_confirmation(self, to: str, link: str) -> None: ...


class ConsoleMailer:
    """Dev-режим: письмо выводится в лог; sent хранит пары (адрес, ссылка) для смоков."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def send_confirmation(self, to: str, link: str) -> None:
        self.sent.append((to, link))
        logger.info("email_confirmation_dev", to=to, link=link)


class SmtpMailer:
    def __init__(self, settings: IdentitySettings) -> None:
        self._settings = settings

    async def send_confirmation(self, to: str, link: str) -> None:
        settings = self._settings
        message = EmailMessage()
        message["From"] = settings.smtp_from or "noreply@ai-dnd.local"
        message["To"] = to
        message["Subject"] = "AI DnD: подтвердите email"
        message.set_content(
            "Добро пожаловать в AI DnD!\n\n"
            f"Подтвердите адрес, перейдя по ссылке:\n{link}\n\n"
            "Если вы не регистрировались, просто проигнорируйте это письмо."
        )
        try:
            await aiosmtplib.send(
                message,
                hostname=settings.smtp_host,
                port=settings.smtp_port,
                username=settings.smtp_user,
                password=(
                    settings.smtp_password.get_secret_value() if settings.smtp_password else None
                ),
                start_tls=settings.smtp_port != 465,
                use_tls=settings.smtp_port == 465,
            )
        except Exception:
            # Ошибка SMTP не должна валить регистрацию — только лог.
            logger.exception("smtp_send_failed", to=to)


def build_mailer(settings: IdentitySettings) -> Mailer:
    if settings.smtp_host:
        return SmtpMailer(settings)
    return ConsoleMailer()
