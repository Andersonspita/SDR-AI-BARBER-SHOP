from __future__ import annotations
import hashlib
from typing import List
from backend.app.models import BarbershopLead, LeadStatus
from backend.app.agents.copywriter import generate_outbound_copy


def calculate_antiban_schedule(
    leads: List[BarbershopLead],
    daily_limit: int = 50,
    min_delay_sec: int = 45,
    max_delay_sec: int = 180,
) -> List[dict]:
    """
    Agente 3: Execution & Anti-Ban Cadence Engine.
    - Régua D0 -> D2 -> D5
    - Intervalos randômicos determinísticos/seguros entre 45s e 180s
    - Respeita limite diário por chip/instância e ignora DO_NOT_CONTACT
    """
    eligible = [
        l for l in leads if l.status != LeadStatus.DO_NOT_CONTACT
    ][: max(0, int(daily_limit))]

    schedule: List[dict] = []
    cumulative_sec = 0

    for lead in eligible:
        if lead.status in (LeadStatus.RAW, LeadStatus.ENRICHED):
            touchpoint = "D0"
        elif lead.status == LeadStatus.CONTACTED_D1:
            touchpoint = "D2"
        else:
            touchpoint = "D5"

        # Gera atraso randômico entre 45s e 180s
        digest = int(hashlib.sha256(f"{lead.id}:{touchpoint}".encode()).hexdigest()[:8], 16)
        span = max(1, (max_delay_sec - min_delay_sec + 1))
        delay_sec = min_delay_sec + (digest % span)
        cumulative_sec += delay_sec

        schedule.append(
            {
                "lead_id": lead.id,
                "shop_name": lead.shop_name,
                "phone": lead.phone,
                "touchpoint": touchpoint,
                "delay_seconds": delay_sec,
                "scheduled_offset_seconds": cumulative_sec,
                "message_preview": generate_outbound_copy(lead, touchpoint=touchpoint),
            }
        )

    return schedule
