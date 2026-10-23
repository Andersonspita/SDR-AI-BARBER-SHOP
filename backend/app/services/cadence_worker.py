"""
Agente 3 em execução: worker que dispara sozinho os toques D2 e D5 da régua.

Régua: D0 (manual, pelo painel) -> D2 (2 dias depois) -> D5 (3 dias após o D2). Qualquer resposta
do barbeiro tira o lead da régua (a conversa passa para os Agentes 4/5), assim como o descadastro.

Proteções anti-ban: intervalo aleatório entre envios (ANTIBAN_MIN/MAX_DELAY_SEC), limite diário
por instância, janela de horário comercial em Brasília e trava de leads fictícios.
"""
from __future__ import annotations
import asyncio
import logging
import os
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, FrozenSet, Optional
from backend.app.agents.copywriter import generate_outbound_copy
from backend.app.models import BarbershopLead, LeadStatus
from backend.app.services.messaging import mark_without_whatsapp, send_to_lead
from backend.app.store import BRT, SDRStore


logger = logging.getLogger(__name__)

# Toque enviado -> (próximo toque, espera até ele)
NEXT_TOUCH = {"D0": ("D2", timedelta(days=2)), "D2": ("D5", timedelta(days=3))}
ACTIVE_CADENCE_STATUSES = (LeadStatus.CONTACTED_D1, LeadStatus.FOLLOWUP_SENT)
RETRY_AFTER_FAILURE = timedelta(minutes=30)


def _parse_range(raw: str, default: tuple) -> tuple:
    try:
        start, end = (int(part) for part in raw.split("-", 1))
        return start, end
    except (ValueError, AttributeError):
        return default


@dataclass(frozen=True)
class CadenceConfig:
    enabled: bool = True
    min_delay_sec: float = 45
    max_delay_sec: float = 180
    poll_interval_sec: float = 30
    send_start_hour: int = 8  # inclusive, horário de Brasília
    send_end_hour: int = 20  # exclusivo
    send_weekdays: FrozenSet[int] = field(default_factory=lambda: frozenset(range(0, 6)))  # seg(0)..sáb(5)

    @classmethod
    def from_env(cls) -> "CadenceConfig":
        start_hour, end_hour = _parse_range(os.getenv("CADENCE_SEND_HOURS", "8-20"), (8, 20))
        first_day, last_day = _parse_range(os.getenv("CADENCE_SEND_WEEKDAYS", "0-5"), (0, 5))
        min_delay = float(os.getenv("ANTIBAN_MIN_DELAY_SEC", "45"))
        return cls(
            enabled=os.getenv("CADENCE_WORKER_ENABLED", "true").lower() == "true",
            min_delay_sec=min_delay,
            max_delay_sec=max(min_delay, float(os.getenv("ANTIBAN_MAX_DELAY_SEC", "180"))),
            poll_interval_sec=float(os.getenv("CADENCE_POLL_INTERVAL_SEC", "30")),
            send_start_hour=start_hour,
            send_end_hour=end_hour,
            send_weekdays=frozenset(range(first_day, last_day + 1)),
        )

    def is_within_send_window(self, now: datetime) -> bool:
        local = now.astimezone(BRT)
        return local.weekday() in self.send_weekdays and self.send_start_hour <= local.hour < self.send_end_hour


def schedule_after_touch(lead: BarbershopLead, step: str, sent_at: datetime) -> None:
    """Marca o toque enviado e agenda o próximo (ou encerra a régua após o D5)."""
    lead.cadence_step = step
    next_touch = NEXT_TOUCH.get(step)
    lead.next_touch_at = (sent_at + next_touch[1]).isoformat() if next_touch else None


def stop_cadence(lead: BarbershopLead) -> None:
    lead.next_touch_at = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CadenceWorker:
    """Processa um lead vencido por vez; `run_forever` espaça os envios com o delay anti-ban."""

    def __init__(
        self,
        store: SDRStore,
        config: CadenceConfig,
        clock: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.store = store
        self.config = config
        self.clock = clock
        self.sleep = sleep
        self.rng = rng or random.Random()
        self.is_leader = False
        self._task: Optional[asyncio.Task] = None

    async def run_once(self) -> dict:
        now = self.clock()
        if not self.config.is_within_send_window(now):
            return {"status": "outside_window"}
        if await self.store.daily_sent_count() >= self.store.daily_limit:
            return {"status": "daily_limit"}

        due = await self.store.list_due_leads(now, limit=1)
        if not due:
            return {"status": "idle"}
        lead = due[0]

        if lead.status not in ACTIVE_CADENCE_STATUSES or lead.cadence_step not in NEXT_TOUCH:
            stop_cadence(lead)
            await self.store.save_lead(lead)
            return {"status": "skipped", "lead_id": lead.id, "reason": f"status {lead.status.value} fora da régua"}

        step = NEXT_TOUCH[lead.cadence_step][0]
        node = f"3.Cadence:{step}"
        delivery = await send_to_lead(lead, generate_outbound_copy(lead, touchpoint=step), node=node)

        if delivery["blocked"]:
            stop_cadence(lead)
            await self.store.save_lead(lead)
            return {"status": "blocked", "lead_id": lead.id, "reason": delivery["error"]}
        if delivery.get("no_whatsapp"):
            mark_without_whatsapp(lead)
            await self.store.save_lead(lead)
            return {"status": "no_whatsapp", "lead_id": lead.id, "reason": delivery["error"]}
        if not delivery["delivered"]:
            lead.next_touch_at = (now + RETRY_AFTER_FAILURE).isoformat()
            await self.store.save_lead(lead)
            logger.warning("Falha no %s para %s; nova tentativa em 30 min: %s", step, lead.shop_name, delivery["error"])
            return {"status": "failed", "lead_id": lead.id, "reason": delivery["error"]}

        lead.status = LeadStatus.FOLLOWUP_SENT
        lead.langgraph_node = node
        schedule_after_touch(lead, step, now)
        await self.store.save_lead(lead)
        logger.info("Cadência %s enviada para %s", step, lead.shop_name)
        return {"status": "sent", "lead_id": lead.id, "touchpoint": step}

    async def run_forever(self) -> None:
        while True:
            try:
                if not self.is_leader:
                    self.is_leader = await self.store.try_acquire_worker_lock()
                result = await self.run_once() if self.is_leader else {"status": "standby"}
            except asyncio.CancelledError:
                raise
            except Exception:  # o loop não pode morrer por uma falha pontual de banco/rede
                logger.exception("Erro no worker de cadência")
                result = {"status": "error"}

            if result["status"] == "sent":
                await self.sleep(self.rng.uniform(self.config.min_delay_sec, self.config.max_delay_sec))
            else:
                await self.sleep(self.config.poll_interval_sec)

    def start(self) -> None:
        if self.config.enabled and self._task is None:
            self._task = asyncio.create_task(self.run_forever(), name="cadence-worker")
            logger.info("Worker de cadência iniciado (janela %dh-%dh BRT)", self.config.send_start_hour, self.config.send_end_hour)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
