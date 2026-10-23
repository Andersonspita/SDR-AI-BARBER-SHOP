"""
Recebe o ciclo de vida do teste grátis e da assinatura enviado pelo barber-shop (`/webhook/barbershop`).

Para barbearias que vieram do SDR, o barber-shop não avisa o dono pelo WhatsApp dele: cada evento
chega aqui e vira uma mensagem na conversa que o SDR já tem com o dono (que trata a resposta).

Assinatura: HMAC-SHA256 de `"{timestamp}.{corpo}"` com `BARBERSHOP_WEBHOOK_SECRET`, nos headers
`X-Gerente-Barber-Timestamp` e `X-Gerente-Barber-Signature: sha256=<hex>`. Avisos com mais de
5 minutos são recusados (proteção contra reenvio de uma requisição capturada).
"""
from __future__ import annotations
import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple
from backend.app.models import BarbershopLead, LeadStatus, TrialStatus
from backend.app.store import BRT
from backend.app.services.onboarding import welcome_messages


MAX_CLOCK_SKEW_SEC = 300

EVENT_TO_TRIAL_STATUS = {
    "trial.activated": TrialStatus.ACTIVATED,
    "trial.expiring": TrialStatus.EXPIRING,
    "trial.expired": TrialStatus.EXPIRED,
    "subscription.activated": TrialStatus.CONVERTED,
}

# Eventos de cobrança: não mudam o teste, só a situação da assinatura.
BILLING_EVENTS = {
    "subscription.invoice_created",
    "subscription.past_due",
    "subscription.suspended",
    "subscription.reactivated",
}

# Fatura gerada ou pagamento resolvido: ninguém mais precisa gerar cobrança à mão.
EVENTS_THAT_RESOLVE_BILLING_HELP = {"subscription.invoice_created", "subscription.activated", "subscription.reactivated"}


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


@dataclass(frozen=True)
class EventOutcome:
    known: bool
    # Mensagens para o dono na conversa do SDR, na ordem de envio (vazio = evento sem aviso).
    messages: Tuple[str, ...] = ()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _when(value: Optional[str]) -> str:
    """
    "29/10" ou "29/10 às 10:00". O fim do teste e a pausa do agendamento vêm do barber-shop como
    dia puro (meia-noite UTC): convertidos para Brasília, cairiam às 21h do dia anterior.
    """
    moment = _parse_iso(value)
    if not moment:
        return "em breve"
    if (moment.hour, moment.minute, moment.second) == (0, 0, 0) and moment.utcoffset().total_seconds() == 0:
        return moment.strftime("%d/%m")
    return moment.astimezone(BRT).strftime("%d/%m às %H:%M")


def _sync_shop(lead: BarbershopLead, shop: dict) -> None:
    """Copia para o lead a situação da barbearia que vem em todo aviso."""
    lead.trial_shop_id = shop.get("id") or lead.trial_shop_id
    if shop.get("slug"):
        lead.slug = shop["slug"]
    if shop.get("url"):
        lead.preview_url = shop["url"]
    if shop.get("trialEndsAt"):
        lead.trial_ends_at = _parse_iso(shop["trialEndsAt"]).isoformat()
    lead.subscription_status = shop.get("subscriptionStatus") or lead.subscription_status
    lead.plan_code = shop.get("plan") or lead.plan_code
    lead.subscribe_url = shop.get("subscribeUrl") or lead.subscribe_url
    # Fatura e prazo de suspensão refletem o estado atual: ausentes = não há.
    lead.invoice_url = shop.get("invoiceUrl")
    suspends_at = _parse_iso(shop.get("suspendsAt"))
    lead.suspends_at = suspends_at.isoformat() if suspends_at else None


def build_notice(event: str, lead: BarbershopLead, shop: dict) -> Optional[str]:
    """Texto do aviso ao dono, no mesmo tom da conversa de venda."""
    name = shop.get("name") or lead.shop_name
    invoice = shop.get("invoiceUrl")
    subscribe = shop.get("subscribeUrl") or lead.subscribe_url
    # Sem pagamento online, a fatura e o jeito de pagar ficam na tela de Assinatura do painel.
    if invoice:
        pay_line = f"Dá pra pagar por aqui (Pix, boleto ou cartão): {invoice}"
    elif subscribe:
        pay_line = f"A fatura e como pagar estão no seu painel: {subscribe}"
    else:
        pay_line = "Me responde aqui que eu te mando como pagar."
    # O atraso nunca tira a página do ar: só o agendamento online pausa.
    pause_line = (
        f"Se não cair até {_when(shop.get('suspendsAt'))}, o agendamento online fica pausado "
        "(a página, a agenda e o painel continuam funcionando)."
    )

    if event == "trial.expiring":
        return (
            f"Opa! O teste grátis da {name} acaba em {_when(shop.get('trialEndsAt'))}.\n"
            f"Pra seguir com a agenda online, é só pagar a primeira mensalidade. {pay_line}\n"
            "Qualquer dúvida sobre os planos, me chama aqui."
        )
    if event == "trial.expired":
        return (
            f"O teste grátis da {name} terminou e a primeira mensalidade venceu. {pay_line}\n" + pause_line
        )
    if event == "subscription.invoice_created":
        return f"A mensalidade do Gerente Barber da {name} tá disponível. {pay_line}"
    if event == "subscription.activated":
        return f"Pagamento confirmado! A assinatura da {name} tá ativa. Bom trabalho aí! ✂️"
    if event == "subscription.past_due":
        return f"A mensalidade do Gerente Barber da {name} venceu. {pay_line}\n" + pause_line
    if event == "subscription.suspended":
        return (
            f"O agendamento online da {name} foi pausado por falta de pagamento. A página, a agenda já "
            "marcada e o painel continuam funcionando.\n"
            + (f"Assim que a fatura for paga, volta na hora: {invoice}" if invoice else pay_line)
        )
    if event == "subscription.reactivated":
        return f"Pagamento recebido, valeu! A {name} tá em dia e o agendamento online tá liberado."
    return None


def apply_event(lead: BarbershopLead, event: str, shop: dict) -> EventOutcome:
    """Atualiza o lead conforme o aviso e devolve a mensagem para o dono (se houver)."""
    trial_status = EVENT_TO_TRIAL_STATUS.get(event)
    if trial_status is None and event not in BILLING_EVENTS:
        return EventOutcome(known=False)

    _sync_shop(lead, shop)
    if trial_status is not None:
        lead.trial_status = trial_status
        lead.langgraph_node = f"5.TrialActivator:{trial_status.value.title()}"
    else:
        lead.langgraph_node = f"5.Billing:{event.split('.', 1)[1].title().replace('_', '')}"
    if trial_status == TrialStatus.CONVERTED:
        lead.status = LeadStatus.CONVERTED
    if event in EVENTS_THAT_RESOLVE_BILLING_HELP:
        lead.needs_billing_help = False
    if event == "trial.activated":
        # Primeiro acesso ao painel: passo a passo, manual e como divulgar o link.
        name = shop.get("name") or lead.shop_name
        return EventOutcome(known=True, messages=tuple(welcome_messages(name, shop.get("url") or lead.preview_url)))
    notice = build_notice(event, lead, shop)
    return EventOutcome(known=True, messages=(notice,) if notice else ())
