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


def recommend_plan(estimated_barbers: int) -> dict:
    """Retorna o plano e foco de pitch segundo a Matriz de Preços e Segmentação."""
    chairs = max(1, int(estimated_barbers or 1))
    if chairs == 1:
        return {
            "plan_name": "Plano Solo",
            "price_range": "R$ 49,90 a R$ 59,90/mês",
            "monthly_anchor": "R$ 49,90",
            "pitch_focus": "agendamento 24/7 e fim do caderno/agenda manual",
        }
    if 2 <= chairs <= 4:
        return {
            "plan_name": "Plano Duo/Squad",
            "price_range": "R$ 99,90 a R$ 129,90/mês",
            "monthly_anchor": "R$ 99,90",
            "pitch_focus": "cálculo de comissão sem brigas no final do mês e controle de estoque",
        }
    if 5 <= chairs <= 9:
        return {
            "plan_name": "Plano Club/Pro",
            "price_range": "R$ 179,90 a R$ 219,90/mês",
            "monthly_anchor": "R$ 179,90",
            "pitch_focus": "painel financeiro DRE, redução de no-show e clube de assinaturas",
        }
    return {
        "plan_name": "Plano Empire",
        "price_range": "R$ 299,90 a R$ 389,90/mês",
        "monthly_anchor": "R$ 299,90",
        "pitch_focus": "split de pagamento automático (Lei do Salão Parceiro) e gestão por unidade",
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
