"""
Recebe o ciclo de vida do teste grátis enviado pelo barber-shop (`/webhook/barbershop`).

Assinatura: HMAC-SHA256 de `"{timestamp}.{corpo}"` com `BARBERSHOP_WEBHOOK_SECRET`, nos headers
`X-Gerente-Barber-Timestamp` e `X-Gerente-Barber-Signature: sha256=<hex>`. Avisos com mais de
5 minutos são recusados (proteção contra reenvio de uma requisição capturada).
"""
from __future__ import annotations
import hashlib
import hmac
import os
import time
from datetime import datetime
from typing import Optional
from backend.app.models import BarbershopLead, LeadStatus, TrialStatus


MAX_CLOCK_SKEW_SEC = 300

EVENT_TO_TRIAL_STATUS = {
    "trial.activated": TrialStatus.ACTIVATED,
    "trial.expiring": TrialStatus.EXPIRING,
    "trial.expired": TrialStatus.EXPIRED,
    "subscription.activated": TrialStatus.CONVERTED,
}


def webhook_secret() -> str:
    return os.getenv("BARBERSHOP_WEBHOOK_SECRET", "").strip()


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def signature_problem(
    secret: str, timestamp: Optional[str], signature: Optional[str], body: bytes, now: Optional[float] = None
) -> Optional[str]:
    """Motivo da recusa, ou None se a assinatura é válida e recente."""
    if not secret:
        return "BARBERSHOP_WEBHOOK_SECRET não configurado"
    if not timestamp or not signature or not signature.startswith("sha256="):
        return "assinatura ausente"
    try:
        age = abs((now if now is not None else time.time()) - int(timestamp))
    except ValueError:
        return "timestamp inválido"
    if age > MAX_CLOCK_SKEW_SEC:
        return "aviso expirado"
    if not hmac.compare_digest(signature[len("sha256="):], sign(secret, timestamp, body)):
        return "assinatura inválida"
    return None


def apply_event(lead: BarbershopLead, event: str, shop: dict) -> bool:
    """Atualiza o lead conforme o aviso. Devolve False para eventos desconhecidos."""
    trial_status = EVENT_TO_TRIAL_STATUS.get(event)
    if trial_status is None:
        return False

    lead.trial_status = trial_status
    lead.trial_shop_id = shop.get("id") or lead.trial_shop_id
    if shop.get("slug"):
        lead.slug = shop["slug"]
    if shop.get("trialEndsAt"):
        lead.trial_ends_at = datetime.fromisoformat(shop["trialEndsAt"].replace("Z", "+00:00")).isoformat()
    lead.langgraph_node = f"5.TrialActivator:{trial_status.value.title()}"
    if trial_status == TrialStatus.CONVERTED:
        lead.status = LeadStatus.CONVERTED
    return True
