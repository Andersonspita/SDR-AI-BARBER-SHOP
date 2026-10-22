"""Agente 5 (execução): libera o teste grátis no barber-shop e monta a mensagem para o dono."""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Optional
from backend.app.models import BarbershopLead, LeadStatus, SAAS_BASE_URL, TrialStatus
from backend.app.services import barbershop_platform as platform_module
from backend.app.services.barbershop_platform import ProvisionedTrial, ProvisioningError


logger = logging.getLogger(__name__)

__all__ = ["ProvisioningError", "activate_trial", "trial_block_reason", "build_trial_message"]


def trial_block_reason(lead: BarbershopLead) -> Optional[str]:
    """Motivo para NÃO liberar o teste agora, ou None se pode liberar."""
    client = platform_module.barbershop_platform
    if lead.status == LeadStatus.DO_NOT_CONTACT:
        return "Lead pediu para não ser contatado"
    if not client.enabled:
        return "Integração com o barber-shop não configurada (BARBERSHOP_API_URL / BARBERSHOP_PROVISIONING_KEY)"
    if lead.is_synthetic and not client.allow_synthetic:
        # Criaria uma barbearia de verdade para um telefone inventado.
        return "Lead fictício: teste grátis bloqueado (BARBERSHOP_ALLOW_SYNTHETIC_TRIALS=false)"
    if not lead.email:
        return "Falta o e-mail do dono (login no barber-shop)"
    return None


def build_trial_message(lead: BarbershopLead, trial: ProvisionedTrial) -> str:
    days = platform_module.barbershop_platform.trial_days
    if trial.setup_url:
        return (
            f"Prontinho! A página da {lead.shop_name} já tá no ar: {trial.url}\n"
            f"Crie sua senha por este link (vale por 72h): {trial.setup_url}\n"
            f"São {days} dias grátis, já com Corte, Barba e Combo cadastrados pra você ajustar."
        )
    return (
        f"Sua página da {lead.shop_name} tá no ar: {trial.url}\n"
        f"É só entrar com o e-mail {lead.email}. Se esqueceu a senha, use o \"esqueci minha senha\" na tela de login."
    )


async def activate_trial(lead: BarbershopLead) -> ProvisionedTrial:
    """
    Provisiona no barber-shop e atualiza o lead (slug final, link, status TRIAL_STARTED).
    Não salva nem envia mensagem: quem chama decide (webhook responde, painel envia).
    """
    reason = trial_block_reason(lead)
    if reason:
        raise ProvisioningError(reason)

    trial = await platform_module.barbershop_platform.provision_trial(lead)
    lead.trial_shop_id = trial.shop_id
    lead.slug = trial.slug
    lead.preview_url = trial.url or f"{SAAS_BASE_URL}/{trial.slug}"
    lead.trial_ends_at = _iso(trial.trial_ends_at)
    if lead.trial_status is None:
        lead.trial_status = TrialStatus.PROVISIONED
    lead.status = LeadStatus.TRIAL_STARTED
    lead.langgraph_node = "5.TrialActivator:Provisioned"
    logger.info("Teste grátis liberado para %s em %s", lead.shop_name, trial.url)
    return trial


def _iso(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
