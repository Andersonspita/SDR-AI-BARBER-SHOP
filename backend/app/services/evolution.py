from __future__ import annotations
import logging
import os
import uuid
from dataclasses import dataclass
from typing import Optional
import httpx


logger = logging.getLogger(__name__)

NO_WHATSAPP_ERROR = "Esse número não tem WhatsApp"


def _says_number_not_on_whatsapp(response: httpx.Response) -> bool:
    """A Evolution responde 400 com `"exists": false` quando o número não tem conta no WhatsApp."""
    try:
        return '"exists":false' in response.text.replace(" ", "")
    except Exception:  # corpo ilegível: trata como erro comum
        return False


@dataclass
class EvolutionClient:
    """
    Conector da Evolution API (WhatsApp). Com WHATSAPP_PROVIDER=MOCK nada sai da máquina:
    o envio é simulado e registrado no log, permitindo rodar o SDR-AI sem instância ativa.
    """

    provider: str = "MOCK"
    base_url: str = "http://localhost:8080"
    instance: str = "gerente-barber-sdr"
    api_key: str = ""
    timeout_sec: float = 10.0

    @classmethod
    def from_env(cls) -> "EvolutionClient":
        return cls(
            provider=os.getenv("WHATSAPP_PROVIDER", "MOCK").upper(),
            base_url=os.getenv("EVOLUTION_BASE_URL", "http://localhost:8080").rstrip("/"),
            instance=os.getenv("EVOLUTION_INSTANCE", "gerente-barber-sdr"),
            api_key=os.getenv("EVOLUTION_API_KEY", ""),
        )

    async def send_text(self, phone: str, text: str) -> dict:
        """Envia texto para o número; nunca levanta exceção, devolve `delivered` e `error`."""
        if self.provider == "MOCK":
            logger.info("[MOCK WhatsApp] -> %s: %s", phone, text.replace("\n", " | "))
            return {"delivered": True, "provider": "MOCK", "message_id": f"mock-{uuid.uuid4()}", "error": None}

        url = f"{self.base_url}/message/sendText/{self.instance}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                response = await client.post(
                    url,
                    json={"number": phone, "text": text},
                    headers={"apikey": self.api_key},
                )
                if response.status_code == 400 and _says_number_not_on_whatsapp(response):
                    logger.warning("Número %s não tem WhatsApp; mensagem não enviada.", phone)
                    return {
                        "delivered": False,
                        "provider": self.provider,
                        "message_id": None,
                        "error": NO_WHATSAPP_ERROR,
                        "no_whatsapp": True,
                    }
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Falha ao enviar WhatsApp para %s via Evolution: %s", phone, exc)
            return {"delivered": False, "provider": self.provider, "message_id": None, "error": str(exc)}

        message_id = (body.get("key") or {}).get("id") if isinstance(body, dict) else None
        return {"delivered": True, "provider": self.provider, "message_id": message_id, "error": None}

    async def numbers_on_whatsapp(self, phones: list[str]) -> Optional[set[str]]:
        """
        Quais telefones têm WhatsApp (consulta, não envia nada). Em MOCK, todos. Se a Evolution não
        responder, devolve None: quem chama mantém os números, e o envio é que vai dizer.
        """
        if self.provider == "MOCK" or not phones:
            return set(phones)
        url = f"{self.base_url}/chat/whatsappNumbers/{self.instance}"
        try:
            async with httpx.AsyncClient(timeout=max(self.timeout_sec, 30.0)) as client:
                response = await client.post(url, json={"numbers": phones}, headers={"apikey": self.api_key})
                response.raise_for_status()
                rows = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Não deu para conferir quais números têm WhatsApp: %s", exc)
            return None
        return {str(row.get("number")) for row in rows if isinstance(row, dict) and row.get("exists")}


evolution_client = EvolutionClient.from_env()
