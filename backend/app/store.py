from __future__ import annotations
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from backend.app.models import BarbershopLead, Interaction, LeadStatus, TechMaturity
from backend.app.repositories import InMemoryRepository, LeadRepository


logger = logging.getLogger(__name__)

# Horário de Brasília (sem horário de verão desde 2019): o limite anti-ban zera à meia-noite local.
BRT = timezone(timedelta(hours=-3))
TRIAL_MODE_SETTING = "trial_mode"

OBJECTION_LABELS = {
    "CADERNO": "Já uso caderno",
    "CLIENTES_VELHOS": "Clientes só usam Whats",
    "SEM_TEMPO": "Sem tempo p/ cadastrar",
    "CONCORRENTE": "Já usa concorrente",
}


def start_of_day_brt(now: Optional[datetime] = None) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(BRT)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def build_metrics(
    leads: List[BarbershopLead],
    replied_lead_ids: set,
    trial_mode: str,
    daily_sent_count: int,
    daily_limit: int,
) -> dict:
    """KPIs do painel calculados a partir dos leads e das respostas recebidas."""
    contacted = [l for l in leads if l.status not in (LeadStatus.RAW, LeadStatus.ENRICHED)]
    response_rate = round(len(replied_lead_ids) / len(contacted) * 100, 1) if contacted else 0.0

    objection_counts: Dict[str, int] = {}
    for lead in leads:
        if lead.last_objection_type:
            objection_counts[lead.last_objection_type] = objection_counts.get(lead.last_objection_type, 0) + 1

    top_objection = "Nenhuma ainda"
    if objection_counts:
        top_key = max(objection_counts, key=objection_counts.get)
        pct = int(round(objection_counts[top_key] / sum(objection_counts.values()) * 100))
        top_objection = f"{OBJECTION_LABELS.get(top_key, top_key)} ({pct}%)"

    return {
        "total_leads": len(leads),
        "contacted_leads": len(contacted),
        "replied_leads": len(replied_lead_ids),
        "response_rate_pct": response_rate,
        "manual_whatsapp_count": sum(1 for l in leads if l.current_system == TechMaturity.MANUAL_WHATSAPP),
        "competitor_count": sum(1 for l in leads if l.current_system == TechMaturity.COMPETITOR),
        "trial_ready_count": sum(
            1 for l in leads if l.status in (LeadStatus.INTERESTED, LeadStatus.TRIAL_STARTED, LeadStatus.CONVERTED)
        ),
        "converted_count": sum(1 for l in leads if l.status == LeadStatus.CONVERTED),
        # Clientes que pedem ação: mensalidade vencida/suspensa ou cobrança a gerar
        "billing_attention_count": sum(
            1 for l in leads if l.needs_billing_help or l.subscription_status in ("PAST_DUE", "SUSPENDED")
        ),
        "active_trials_count": sum(
            1 for l in leads if l.trial_status and l.trial_status.value in ("PROVISIONED", "ACTIVATED", "EXPIRING")
        ),
        "objection_breakdown": objection_counts,
        "top_objection": top_objection,
        "trial_mode": trial_mode,
        "daily_sent_count": daily_sent_count,
        "daily_limit": daily_limit,
    }


class SDRStore:
    """
    Fachada de persistência do SDR-AI. Usa PostgreSQL quando `DATABASE_URL` está definido e acessível;
    caso contrário (ou com `STORAGE_BACKEND=memory`) cai para o repositório em memória.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Volta ao repositório em memória vazio com a configuração do .env (usado nos testes)."""
        self.repo: LeadRepository = InMemoryRepository()
        self.default_trial_mode: str = os.getenv("TRIAL_MODE", "STAND_BY")  # STAND_BY | AUTO_LINK
        self.daily_limit: int = int(os.getenv("ANTIBAN_DAILY_LIMIT_PER_INSTANCE", "50"))

    @property
    def backend_name(self) -> str:
        return self.repo.backend_name

    async def connect(self) -> None:
        """Escolhe o backend na inicialização da API. Falha no Postgres nunca derruba a API."""
        database_url = os.getenv("DATABASE_URL", "").strip()
        backend = os.getenv("STORAGE_BACKEND", "auto").lower()
        if backend == "memory" or not database_url:
            logger.info("Persistência: memória (sem DATABASE_URL ou STORAGE_BACKEND=memory)")
            return

        try:
            # Import tardio: o modo memória não exige asyncpg instalado.
            from backend.app.repositories.postgres import PostgresRepository

            attempts = int(os.getenv("DATABASE_CONNECT_ATTEMPTS", "3"))
            self.repo = await PostgresRepository.connect(database_url, attempts=attempts)
            logger.info("Persistência: PostgreSQL")
        except Exception as exc:  # qualquer falha de driver/rede/schema deve cair no fallback
            if backend == "postgres":
                raise
            logger.warning("Postgres indisponível, usando memória (dados não persistem): %s", exc)
            self.repo = InMemoryRepository()

    async def close(self) -> None:
        await self.repo.close()

    async def upsert_lead(self, lead: BarbershopLead) -> BarbershopLead:
        return await self.repo.upsert_lead(lead)

    async def save_lead(self, lead: BarbershopLead) -> None:
        await self.repo.save_lead(lead)

    async def get_lead(self, lead_id: str) -> Optional[BarbershopLead]:
        return await self.repo.get_lead(lead_id)

    async def get_lead_by_phone(self, phone: str) -> Optional[BarbershopLead]:
        return await self.repo.get_lead_by_phone(phone)

    async def list_leads(self, state: Optional[str] = None, city: Optional[str] = None) -> List[BarbershopLead]:
        return await self.repo.list_leads(state=state, city=city)

    async def add_interaction(self, interaction: Interaction) -> Interaction:
        return await self.repo.add_interaction(interaction)

    async def get_lead_interactions(self, lead_id: str) -> List[Interaction]:
        return await self.repo.get_lead_interactions(lead_id)

    async def list_due_leads(self, now: datetime, limit: int = 10) -> List[BarbershopLead]:
        return await self.repo.list_due_leads(now, limit=limit)

    async def try_acquire_worker_lock(self) -> bool:
        return await self.repo.try_acquire_worker_lock()

    async def mark_event_processed(self, event_id: str) -> bool:
        return await self.repo.mark_event_processed(event_id)

    async def daily_sent_count(self) -> int:
        """Envios OUTBOUND desde a meia-noite (BRT), base do limite anti-ban por instância."""
        return await self.repo.count_outbound_since(start_of_day_brt())

    async def get_trial_mode(self) -> str:
        return await self.repo.get_setting(TRIAL_MODE_SETTING) or self.default_trial_mode

    async def set_trial_mode(self, mode: str) -> None:
        await self.repo.set_setting(TRIAL_MODE_SETTING, mode)

    async def compute_metrics(self) -> dict:
        # Carrega todos os leads: suficiente para milhares de leads; acima disso, trocar por agregações SQL.
        return build_metrics(
            leads=await self.repo.list_leads(),
            replied_lead_ids=await self.repo.replied_lead_ids(),
            trial_mode=await self.get_trial_mode(),
            daily_sent_count=await self.daily_sent_count(),
            daily_limit=self.daily_limit,
        )


store = SDRStore()
