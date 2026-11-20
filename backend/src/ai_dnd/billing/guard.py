"""BillingGuard — обёртка вокруг LLM-шлюза: резерв до вызова, расчёт по факту.

Стиль BudgetGateway (.local/demo_runner.py): до вызова проверяются глобальный
предохранитель, лимит сессии, баланс и лимиты пользователя (атомарно с резервом);
после — списание фактической стоимости и возврат резерва. При отказе — BillingStop
с понятным сообщением. Оборачиваются только AI-вызовы: игра между людьми не блокируется.
ai/gateway.py не меняется — wiring делает Root.
"""

import uuid
from collections.abc import AsyncIterator, Sequence
from decimal import Decimal

from pydantic import BaseModel

from ai_dnd.ai.config import LLMConfig
from ai_dnd.ai.gateway import LLMGateway, Structured
from ai_dnd.ai.recorder import CallContext
from ai_dnd.ai.schemas import LLMResponse, Message, StreamEnd, StreamEvent, ToolChoice, ToolSpec
from ai_dnd.billing.errors import BillingStop
from ai_dnd.billing.ledger import CreditLedger, LedgerRef
from ai_dnd.billing.limits import BillingLimits
from ai_dnd.billing.settings import BillingSettings, get_billing_settings, usd_to_credits


class BillingGuard:
    def __init__(
        self,
        inner: LLMGateway,
        ledger: CreditLedger,
        limits: BillingLimits,
        settings: BillingSettings | None = None,
    ) -> None:
        self._inner = inner
        self._ledger = ledger
        self._limits = limits
        self._settings = settings or get_billing_settings()

    @property
    def config(self) -> LLMConfig:
        return self._inner.config

    async def complete(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: ToolChoice | None = None,
        json_output: bool = False,
        context: CallContext | None = None,
    ) -> LLMResponse:
        ref = await self._before(context)
        try:
            response = await self._inner.complete(
                task,
                messages,
                tools=tools,
                tool_choice=tool_choice,
                json_output=json_output,
                context=context,
            )
        except Exception:
            await self._release(ref)
            raise
        await self._settle(ref, response.cost)
        return response

    async def stream(
        self,
        task: str,
        messages: Sequence[Message],
        *,
        tools: Sequence[ToolSpec] = (),
        tool_choice: ToolChoice | None = None,
        context: CallContext | None = None,
    ) -> AsyncIterator[StreamEvent]:
        ref = await self._before(context)
        settled = False
        try:
            async for event in self._inner.stream(
                task, messages, tools=tools, tool_choice=tool_choice, context=context
            ):
                if isinstance(event, StreamEnd):
                    await self._settle(ref, event.response.cost)
                    settled = True
                yield event
        finally:
            # Поток оборвался (ошибка или ранний выход потребителя): резерв возвращается.
            if not settled:
                await self._release(ref)

    async def structured[T: BaseModel](
        self,
        task: str,
        messages: Sequence[Message],
        schema: type[T],
        *,
        context: CallContext | None = None,
    ) -> Structured[T]:
        ref = await self._before(context)
        try:
            result = await self._inner.structured(task, messages, schema, context=context)
        except Exception:
            await self._release(ref)
            raise
        await self._settle(ref, result.cost)
        return result

    # --- Внутреннее ---

    async def _before(self, context: CallContext | None) -> LedgerRef:
        if context is None or context.user_id is None:
            raise BillingStop(
                "no_payer",
                "Не определён плательщик за ИИ-вызов: нужен user_id в контексте вызова.",
            )
        await self._limits.check_global()
        await self._limits.check_session(context.session_id)
        ref = LedgerRef("llm_call", str(uuid.uuid4()))
        await self._ledger.reserve(
            context.user_id,
            self._settings.reserve_credits,
            ref,
            daily_limit=self._settings.user_daily_spend_credits,
            monthly_limit=self._settings.user_monthly_spend_credits,
        )
        return ref

    async def _settle(self, ref: LedgerRef, cost: Decimal) -> None:
        await self._ledger.settle(ref, usd_to_credits(cost, self._settings.credit_usd))

    async def _release(self, ref: LedgerRef) -> None:
        await self._ledger.refund(ref, self._settings.reserve_credits)
