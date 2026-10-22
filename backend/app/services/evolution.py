from __future__ import annotations
import logging
import os
import uuid
from dataclasses import dataclass
import httpx


logger = logging.getLogger(__name__)


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
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.error("Falha ao enviar WhatsApp para %s via Evolution: %s", phone, exc)
            return {"delivered": False, "provider": self.provider, "message_id": None, "error": str(exc)}

        message_id = (body.get("key") or {}).get("id") if isinstance(body, dict) else None
        return {"delivered": True, "provider": self.provider, "message_id": message_id, "error": None}


evolution_client = EvolutionClient.from_env()
