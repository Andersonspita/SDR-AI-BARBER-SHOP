from __future__ import annotations
from datetime import datetime
from typing import Dict, List, Optional, Set
from backend.app.models import BarbershopLead, Interaction
from backend.app.repositories.base import MIN_PHONE_DIGITS, PHONE_SUFFIX_LENGTH, phone_digits


class InMemoryRepository:
    """Fallback sem banco: tudo some quando o processo reinicia. Ideal para dev e testes."""

    backend_name = "memory"

    def __init__(self) -> None:
        self.leads: Dict[str, BarbershopLead] = {}
        self.interactions: List[Interaction] = []
        self.settings: Dict[str, str] = {}

    async def upsert_lead(self, lead: BarbershopLead) -> BarbershopLead:
        for existing in self.leads.values():
            if existing.phone == lead.phone:
                return existing
        self.leads[lead.id] = lead
        return lead

    async def save_lead(self, lead: BarbershopLead) -> None:
        self.leads[lead.id] = lead

    async def get_lead(self, lead_id: str) -> Optional[BarbershopLead]:
        return self.leads.get(lead_id)

    async def get_lead_by_phone(self, phone: str) -> Optional[BarbershopLead]:
        clean = phone_digits(phone)
        if len(clean) < MIN_PHONE_DIGITS:
            return None
        for lead in self.leads.values():
            lead_clean = phone_digits(lead.phone)
            if lead_clean == clean or lead_clean.endswith(clean[-PHONE_SUFFIX_LENGTH:]):
                return lead
        return None

    async def list_leads(self, state: Optional[str] = None, city: Optional[str] = None) -> List[BarbershopLead]:
        items = list(self.leads.values())
        if state:
            items = [l for l in items if l.state.upper() == state.upper()]
        if city:
            items = [l for l in items if l.city.lower() == city.lower()]
        return items

    async def list_due_leads(self, now: datetime, limit: int = 10) -> List[BarbershopLead]:
        due = [
            l for l in self.leads.values()
            if l.next_touch_at and datetime.fromisoformat(l.next_touch_at) <= now
        ]
        due.sort(key=lambda l: l.next_touch_at)
        return due[:limit]

    async def add_interaction(self, interaction: Interaction) -> Interaction:
        self.interactions.append(interaction)
        return interaction

    async def get_lead_interactions(self, lead_id: str) -> List[Interaction]:
        return [i for i in self.interactions if i.lead_id == lead_id]

    async def count_outbound_since(self, since: datetime) -> int:
        return sum(
            1
            for i in self.interactions
            if i.direction == "OUTBOUND" and datetime.fromisoformat(i.sent_at) >= since
        )

    async def replied_lead_ids(self) -> Set[str]:
        return {i.lead_id for i in self.interactions if i.direction == "INBOUND"}

    async def get_setting(self, key: str) -> Optional[str]:
        return self.settings.get(key)

    async def set_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    async def try_acquire_worker_lock(self) -> bool:
        # Memória só existe dentro de um processo: não há outra réplica para disputar.
        return True

    async def close(self) -> None:
        return None
