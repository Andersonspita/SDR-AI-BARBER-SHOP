"""
Cliente do provisionamento de testes grátis no barber-shop (`POST /provisioning/trials`).

Usa uma chave própria (`BARBERSHOP_PROVISIONING_KEY`), separada da chave da plataforma:
o SDR só consegue criar barbearias em teste, nunca suspender ou alterar as outras.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Optional
import httpx
from backend.app.models import BarbershopLead


logger = logging.getLogger(__name__)



class ProvisioningError(Exception):
    """O barber-shop recusou ou não respondeu ao pedido de teste grátis."""


@dataclass(frozen=True)
class ProvisionedTrial:
    created: bool
    shop_id: str
    slug: str
    url: str
    trial_ends_at: Optional[str]
    # Link de uso único para o dono criar a senha; None se ele já entrou.
    setup_url: Optional[str]


@dataclass(frozen=True)
class BarbershopPlatformClient:
    base_url: str = ""
    provisioning_key: str = ""
    trial_days: int = 7
    allow_synthetic: bool = False
    timeout_sec: float = 15.0

    @classmethod
    def from_env(cls) -> "BarbershopPlatformClient":
        return cls(
            base_url=os.getenv("BARBERSHOP_API_URL", "").strip().rstrip("/"),
            provisioning_key=os.getenv("BARBERSHOP_PROVISIONING_KEY", "").strip(),
            trial_days=int(os.getenv("TRIAL_DAYS", "7")),
            allow_synthetic=os.getenv("BARBERSHOP_ALLOW_SYNTHETIC_TRIALS", "false").lower() == "true",
        )

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.provisioning_key)

    def build_payload(self, lead: BarbershopLead) -> dict:
        return {
            "externalLeadId": lead.id,
            "shopName": lead.shop_name,
            "slug": lead.slug,
            "city": lead.city,
            # Código da tabela de planos do barber-shop (solo, essencial...); ver models.recommend_plan.
            "plan": lead.recommended_plan.get("plan_code", "solo"),
            "trialDays": self.trial_days,
            "adminName": lead.owner_name or f"Responsável {lead.shop_name}",
            "adminEmail": lead.email,
            "adminPhone": lead.phone,
        }

    async def provision_trial(
        self, lead: BarbershopLead, http_client: Optional[httpx.AsyncClient] = None
    ) -> ProvisionedTrial:
        """Cria (ou recupera, se o lead já tinha) a barbearia em teste no barber-shop."""
        if not self.enabled:
            raise ProvisioningError("Integração com o barber-shop não configurada")

        client = http_client or httpx.AsyncClient(timeout=self.timeout_sec)
        try:
            response = await client.post(
                f"{self.base_url}/provisioning/trials",
                json=self.build_payload(lead),
                headers={"X-Provisioning-Key": self.provisioning_key},
            )
        except httpx.HTTPError as exc:
            # Timeouts do httpx não têm mensagem: o nome da exceção é o que diz o que houve.
            raise ProvisioningError(f"barber-shop indisponível ({type(exc).__name__}): {exc}") from exc
        finally:
            if http_client is None:
                await client.aclose()

        if response.status_code not in (200, 201):
            raise ProvisioningError(f"barber-shop recusou ({response.status_code}): {_error_message(response)}")

        body = response.json()
        shop = body["shop"]
        return ProvisionedTrial(
            created=bool(body.get("created")),
            shop_id=shop["id"],
            slug=shop["slug"],
            url=shop["url"],
            trial_ends_at=shop.get("trialEndsAt"),
            setup_url=body.get("setupUrl"),
        )


def _error_message(response: httpx.Response) -> str:
    """Erros do NestJS vêm como {"message": str | [str]}."""
    try:
        message = response.json().get("message", "")
    except ValueError:
        return response.text[:200]
    return "; ".join(message) if isinstance(message, list) else str(message)


barbershop_platform = BarbershopPlatformClient.from_env()
