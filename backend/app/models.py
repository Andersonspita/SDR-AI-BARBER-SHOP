from __future__ import annotations
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import os
import re
import unicodedata
import uuid
from typing import Optional


# Endereço público do barber-shop (vitrines em /{slug}). Mesmo valor do APP_URL de lá.
SAAS_BASE_URL = os.getenv("SAAS_BASE_URL", "https://barber-shop.horizonaj.com.br").rstrip("/")


class LeadStatus(str, Enum):
    RAW = "RAW"
    ENRICHED = "ENRICHED"
    CONTACTED_D1 = "CONTACTED_D1"
    FOLLOWUP_SENT = "FOLLOWUP_SENT"
    INTERESTED = "INTERESTED"
    OBJECTION_RAISED = "OBJECTION_RAISED"
    TRIAL_STARTED = "TRIAL_STARTED"
    CONVERTED = "CONVERTED"
    DO_NOT_CONTACT = "DO_NOT_CONTACT"


class LeadSource(str, Enum):
    """Origem do lead. SYNTHETIC = gerado localmente (telefone inventado): nunca recebe WhatsApp real."""

    SYNTHETIC = "SYNTHETIC"
    GOOGLE_PLACES = "GOOGLE_PLACES"
    APIFY_GOOGLE_MAPS = "APIFY_GOOGLE_MAPS"  # Google Maps pela ferramenta do Apify
    CSV_IMPORT = "CSV_IMPORT"  # planilha importada no painel


class TrialStatus(str, Enum):
    """Teste grátis no barber-shop, atualizado pelos webhooks de lá."""

    PROVISIONED = "PROVISIONED"  # barbearia criada, dono ainda não entrou
    ACTIVATED = "ACTIVATED"  # dono definiu a senha / fez login
    EXPIRING = "EXPIRING"  # faltam 48h
    EXPIRED = "EXPIRED"  # venceu e saiu do ar
    CONVERTED = "CONVERTED"  # plano ativado


class TechMaturity(str, Enum):
    MANUAL_WHATSAPP = "MANUAL_WHATSAPP"
    COMPETITOR = "COMPETITOR"
    UNKNOWN = "UNKNOWN"


def generate_shop_slug(shop_name: str) -> str:
    """Converte o nome da barbearia em slug no padrão https://barber-shop.horizonaj.com.br/{slug}."""
    normalized = unicodedata.normalize("NFKD", shop_name or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")
    return slug or "barbearia"


def get_shop_initials(shop_name: str) -> str:
    words = [w for w in re.split(r"\s+", (shop_name or "").strip()) if w]
    if not words:
        return "GB"
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


# Tabela de planos do Gerente Barber (barber-shop, tabela `plans`), por profissionais ativos.
# Os preços são editáveis lá em /plataforma -> Planos e preços: mudou lá, atualize aqui, porque o
# valor entra na copy do D0. `plan_code` é o código enviado no provisionamento do teste grátis.
PLAN_TIERS = (
    # (até N profissionais, código, nome, mensalidade, foco do pitch)
    (1, "solo", "Plano Solo", "R$ 35,00", "agendamento online 24h e fim da agenda no caderno"),
    (3, "essencial", "Plano Essencial", "R$ 89,90", "agenda da equipe num lugar só e lembrete no WhatsApp contra falta"),
    (6, "profissional", "Plano Profissional", "R$ 149,90", "comissão de cada profissional calculada no financeiro e menos faltas"),
    (10, "premium", "Plano Premium", "R$ 219,90", "financeiro da casa inteira, encaixes e lista de espera"),
)
LARGE_TEAM_PLAN = ("rede", "Plano Rede", "R$ 219,90", "equipe grande: 10 profissionais inclusos + R$ 19,90 por extra")


def recommend_plan(estimated_barbers: int) -> dict:
    """Plano do Gerente Barber que comporta a equipe e o foco do pitch para esse porte."""
    chairs = max(1, int(estimated_barbers or 1))
    code, name, price, focus = next(
        ((code, name, price, focus) for limit, code, name, price, focus in PLAN_TIERS if chairs <= limit),
        LARGE_TEAM_PLAN,
    )
    return {
        "plan_code": code,
        "plan_name": name,
        "price_range": f"{price}/mês" if code != "rede" else f"{price}/mês + R$ 19,90 por profissional extra",
        "monthly_anchor": price,
        "pitch_focus": focus,
    }


@dataclass
class BarbershopLead:
    shop_name: str
    phone: str
    city: str
    state: str
    owner_name: Optional[str] = None
    instagram_handle: Optional[str] = None
    bio_url: Optional[str] = None
    competitor_name: Optional[str] = None
    estimated_barbers: int = 1
    current_system: TechMaturity = TechMaturity.UNKNOWN
    priority: str = "MEDIA"
    status: LeadStatus = LeadStatus.RAW
    neighborhood: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    slug: str = ""
    preview_url: str = ""
    initials: str = ""
    recommended_plan: dict = field(default_factory=dict)
    langgraph_node: str = "1.Scraper"
    last_objection_type: Optional[str] = None
    source: LeadSource = LeadSource.SYNTHETIC
    cadence_step: Optional[str] = None  # último toque enviado: D0 | D2 | D5
    next_touch_at: Optional[str] = None  # ISO 8601; None = fora da régua automática
    email: Optional[str] = None  # login do dono no barber-shop (pedido no WhatsApp)
    trial_shop_id: Optional[str] = None  # id da barbearia criada no barber-shop
    trial_status: Optional[TrialStatus] = None
    trial_ends_at: Optional[str] = None
    # Assinatura no barber-shop (espelho do que chega pelos webhooks de lá)
    subscription_status: Optional[str] = None  # TRIAL | ACTIVE | PAST_DUE | BLOCKED | EXEMPT (antigos: SUSPENDED | EXPIRED)
    plan_code: Optional[str] = None  # SOLO | SQUAD | PRO | EMPIRE
    invoice_url: Optional[str] = None  # fatura em aberto no Asaas
    subscribe_url: Optional[str] = None  # tela de assinatura do painel do dono
    suspends_at: Optional[str] = None  # quando sai do ar se não pagar
    # O dono pediu para pagar e não há fatura: alguém precisa gerar a cobrança na plataforma.
    needs_billing_help: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def __post_init__(self):
        if isinstance(self.status, str):
            self.status = LeadStatus(self.status)
        if isinstance(self.current_system, str):
            self.current_system = TechMaturity(self.current_system)
        if isinstance(self.source, str):
            self.source = LeadSource(self.source)
        if isinstance(self.trial_status, str):
            self.trial_status = TrialStatus(self.trial_status)
        if not self.slug:
            self.slug = generate_shop_slug(self.shop_name)
        if not self.preview_url:
            self.preview_url = f"{SAAS_BASE_URL}/{self.slug}"
        if not self.initials:
            self.initials = get_shop_initials(self.shop_name)
        self.recommended_plan = recommend_plan(self.estimated_barbers)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["current_system"] = self.current_system.value
        data["source"] = self.source.value
        data["trial_status"] = self.trial_status.value if self.trial_status else None
        data["is_synthetic"] = self.is_synthetic
        return data

    @property
    def is_synthetic(self) -> bool:
        return self.source == LeadSource.SYNTHETIC


@dataclass
class Interaction:
    lead_id: str
    direction: str  # INBOUND | OUTBOUND
    message_text: str
    channel: str = "WHATSAPP"
    intent_detected: Optional[str] = None
    langgraph_node: Optional[str] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    sent_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)
