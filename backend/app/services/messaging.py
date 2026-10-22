from __future__ import annotations
import logging
from typing import Optional
from backend.app.models import BarbershopLead, Interaction
from backend.app.services.evolution import evolution_client
from backend.app.store import store


logger = logging.getLogger(__name__)

SYNTHETIC_BLOCK_REASON = (
    "Lead fictício (gerado localmente, telefone inventado): envio real bloqueado. "
    "Configure GOOGLE_MAPS_API_KEY para buscar barbearias reais."
)


def outbound_block_reason(lead: BarbershopLead, provider: Optional[str] = None) -> Optional[str]:
    """
    Trava de segurança de TODO envio de WhatsApp: com um provedor real, lead fictício nunca recebe
    mensagem, porque o telefone dele foi inventado e pode pertencer a uma pessoa de verdade.
    """
    active_provider = (provider or evolution_client.provider).upper()
    if active_provider != "MOCK" and lead.is_synthetic:
        return SYNTHETIC_BLOCK_REASON
    return None


async def send_to_lead(lead: BarbershopLead, text: str, node: str, intent: Optional[str] = None) -> dict:
    """Único caminho de envio para leads: aplica a trava, envia e registra a interação OUTBOUND."""
    block_reason = outbound_block_reason(lead)
    if block_reason:
        logger.warning("Envio bloqueado para o lead %s (%s): %s", lead.id, lead.shop_name, block_reason)
        return {"delivered": False, "blocked": True, "provider": evolution_client.provider, "message_id": None, "error": block_reason}

    delivery = await evolution_client.send_text(lead.phone, text)
    delivery["blocked"] = False
    if delivery["delivered"]:
        await store.add_interaction(
            Interaction(lead_id=lead.id, direction="OUTBOUND", message_text=text, intent_detected=intent, langgraph_node=node)
        )
    return delivery
