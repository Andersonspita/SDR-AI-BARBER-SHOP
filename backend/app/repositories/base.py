from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Protocol, Set
from backend.app.models import BarbershopLead, Interaction


def phone_digits(phone: Optional[str]) -> str:
    return "".join(ch for ch in (phone or "") if ch.isdigit())


# Telefones com menos dígitos que isso não identificam ninguém (evita casar "" com qualquer lead).
MIN_PHONE_DIGITS = 8
# Casa pelo final do número para tolerar DDI/9º dígito ausentes no remoteJid da Evolution API.
PHONE_SUFFIX_LENGTH = 9


class LeadRepository(Protocol):
    """Contrato de persistência de leads, interações e configurações do SDR-AI."""

    backend_name: str

    async def upsert_lead(self, lead: BarbershopLead) -> BarbershopLead:
        """Insere o lead; se o telefone já existir, devolve o lead salvo sem alterá-lo."""
        ...

    async def save_lead(self, lead: BarbershopLead) -> None:
        """Persiste as mudanças de um lead existente (status, nó do grafo, objeção...)."""
        ...

    async def get_lead(self, lead_id: str) -> Optional[BarbershopLead]: ...

    async def get_lead_by_phone(self, phone: str) -> Optional[BarbershopLead]: ...

    async def list_leads(self, state: Optional[str] = None, city: Optional[str] = None) -> List[BarbershopLead]: ...

    async def add_interaction(self, interaction: Interaction) -> Interaction: ...

    async def get_lead_interactions(self, lead_id: str) -> List[Interaction]: ...

    async def list_due_leads(self, now: datetime, limit: int = 10) -> List[BarbershopLead]:
        """Leads com próximo toque da régua vencido (`next_touch_at <= now`), mais antigos primeiro."""
        ...

    async def count_outbound_since(self, since: datetime) -> int: ...

    async def replied_lead_ids(self) -> Set[str]: ...

    async def get_setting(self, key: str) -> Optional[str]: ...

    async def set_setting(self, key: str, value: str) -> None: ...

    async def mark_event_processed(self, event_id: str) -> bool:
        """Registra o id de um webhook. False se ele já tinha sido processado (reenvio)."""
        ...

    async def try_acquire_worker_lock(self) -> bool:
        """Garante um único worker de cadência ativo entre réplicas da API. True = este processo lidera."""
        ...

    async def close(self) -> None: ...
