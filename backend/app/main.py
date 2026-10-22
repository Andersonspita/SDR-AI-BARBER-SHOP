from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Literal, Optional, Tuple
from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from backend.app.models import Interaction, LeadStatus
from backend.app.store import store
from backend.app.auth import AuthSettings, production_auth_problems, require_session, require_webhook_token
from backend.app.auth import router as auth_router
from backend.app.agents.scraper import ScraperError, discover_barbershops
from backend.app.agents.copywriter import generate_outbound_copy
from backend.app.agents.graph import classify_and_run_graph
from backend.app.services.evolution import evolution_client
from backend.app.services.messaging import outbound_block_reason, send_to_lead
from backend.app.services.cadence_worker import CadenceConfig, CadenceWorker, schedule_after_touch, stop_cadence


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def validate_startup_config() -> None:
    """Com WhatsApp real, configuração de segurança fraca impede a API de subir (fail fast)."""
    problems = production_auth_problems(AuthSettings.from_env())
    if evolution_client.provider != "MOCK" and problems:
        raise RuntimeError("Configuração insegura para WHATSAPP_PROVIDER real: " + "; ".join(problems))
    for problem in problems:
        logger.warning("Segurança (aceito só em modo MOCK): %s", problem)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    validate_startup_config()
    await store.connect()
    worker = CadenceWorker(store, CadenceConfig.from_env())
    worker.start()
    try:
        yield
    finally:
        await worker.stop()
        await store.close()


app = FastAPI(title="Gerente Barber SDR-AI", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("FRONTEND_ORIGINS", "http://localhost:3000").split(",")],
    allow_credentials=True,  # cookie de sessão httpOnly do painel
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(auth_router)

# Tudo em /api (exceto /api/auth) exige sessão do admin.
api = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


class ScrapeRequest(BaseModel):
    state: str = Field(min_length=2, max_length=2, description="UF, ex: BA")
    city: str = Field(min_length=2)
    neighborhood: Optional[str] = None


class TrialModeRequest(BaseModel):
    trial_mode: Literal["STAND_BY", "AUTO_LINK"]


class SimulatedInbound(BaseModel):
    phone: str = Field(min_length=8)
    message: str = Field(min_length=1, max_length=2000)


def _extract_inbound(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str], bool]:
    """
    Aceita o formato do simulador ({"phone", "message"}) e o evento `messages.upsert`
    da Evolution API. Retorna (telefone, texto, from_me).
    """
    data = payload.get("data")
    if isinstance(data, dict):
        key = data.get("key") or {}
        phone = (key.get("remoteJid") or "").split("@")[0]
        message = data.get("message") or {}
        text = message.get("conversation") or (message.get("extendedTextMessage") or {}).get("text")
        return phone or None, text, bool(key.get("fromMe"))
    return payload.get("phone"), payload.get("message"), False


async def handle_inbound(phone: str, text: str) -> dict:
    """Agentes 4 + 5: roda o grafo para a resposta do barbeiro e responde no WhatsApp."""
    lead = await store.get_lead_by_phone(phone)
    if not lead:
        logger.info("Mensagem de número desconhecido ignorada: %s", phone)
        return {"status": "ignored", "reason": "lead não encontrado"}

    result = classify_and_run_graph(lead, text, trial_mode=await store.get_trial_mode())
    stop_cadence(lead)  # o barbeiro respondeu: a conversa sai da régua automática
    await store.save_lead(lead)
    await store.add_interaction(
        Interaction(
            lead_id=lead.id,
            direction="INBOUND",
            message_text=text,
            intent_detected=result["intent"],
            langgraph_node=result["langgraph_node"],
        )
    )

    delivery = None
    if result["reply"]:
        delivery = await send_to_lead(lead, result["reply"], node=result["langgraph_node"], intent=result["intent"])
    return {"status": "processed", **result, "delivery": delivery, "lead": lead.to_dict()}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "storage": store.backend_name}


@app.post("/webhook/whatsapp", dependencies=[Depends(require_webhook_token)])
async def whatsapp_webhook(payload: Dict[str, Any]) -> dict:
    """Eventos da Evolution API. Sempre 200 para eventos ignorados, para a Evolution não reenviar."""
    phone, text, from_me = _extract_inbound(payload)
    if from_me or not phone or not text:
        return {"status": "ignored", "reason": "evento sem mensagem recebida"}
    return await handle_inbound(phone, text)


@api.post("/simulator/inbound")
async def simulate_inbound(body: SimulatedInbound) -> dict:
    """Simulador do painel. Só em modo MOCK: com WhatsApp real a resposta iria para o barbeiro de verdade."""
    if evolution_client.provider != "MOCK":
        raise HTTPException(status_code=403, detail="Simulador disponível só com WHATSAPP_PROVIDER=MOCK")
    return await handle_inbound(body.phone, body.message)


@api.post("/leads/scrape")
async def scrape_leads(body: ScrapeRequest) -> dict:
    """Agente 1: descobre barbearias por UF/Cidade e salva (sem duplicar por telefone)."""
    try:
        found = await discover_barbershops(body.state, body.city, body.neighborhood)
    except ScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    saved = [(await store.upsert_lead(lead)).to_dict() for lead in found]
    return {"count": len(saved), "leads": saved}


@api.get("/leads")
async def list_leads(state: Optional[str] = None, city: Optional[str] = None) -> dict:
    leads = [lead.to_dict() for lead in await store.list_leads(state=state, city=city)]
    return {"count": len(leads), "leads": leads}


@api.get("/leads/{lead_id}/interactions")
async def lead_interactions(lead_id: str) -> dict:
    if not await store.get_lead(lead_id):
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    return {"interactions": [i.to_dict() for i in await store.get_lead_interactions(lead_id)]}


@api.get("/cadence/schedule")
async def cadence_schedule() -> dict:
    """Agente 3: fila do worker (próximos D2/D5 agendados) e janela de envio vigente."""
    config = CadenceConfig.from_env()
    queued = sorted((l for l in await store.list_leads() if l.next_touch_at), key=lambda l: l.next_touch_at)
    return {
        "worker_enabled": config.enabled,
        "send_hours": f"{config.send_start_hour}h-{config.send_end_hour}h (Brasília)",
        "remaining_today": max(0, store.daily_limit - await store.daily_sent_count()),
        "queue": [
            {"lead_id": l.id, "shop_name": l.shop_name, "last_step": l.cadence_step, "next_touch_at": l.next_touch_at}
            for l in queued
        ],
    }


@api.post("/leads/{lead_id}/send-d0")
async def send_d0(lead_id: str) -> dict:
    """Agente 2 + 3: envia o gancho inicial (D0) e agenda o D2 no worker."""
    lead = await store.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if lead.status == LeadStatus.DO_NOT_CONTACT:
        raise HTTPException(status_code=409, detail="Lead pediu para não ser contatado")
    block_reason = outbound_block_reason(lead)
    if block_reason:
        raise HTTPException(status_code=409, detail=block_reason)
    if await store.daily_sent_count() >= store.daily_limit:
        raise HTTPException(status_code=429, detail="Limite diário anti-ban atingido")

    text = generate_outbound_copy(lead, touchpoint="D0")
    delivery = await send_to_lead(lead, text, node="3.Cadence:D0")
    if not delivery["delivered"]:
        raise HTTPException(status_code=502, detail=f"Falha no envio WhatsApp: {delivery['error']}")

    lead.status = LeadStatus.CONTACTED_D1
    lead.langgraph_node = "3.Cadence:D0"
    schedule_after_touch(lead, "D0", datetime.now(timezone.utc))
    await store.save_lead(lead)
    return {"message": text, "delivery": delivery, "lead": lead.to_dict()}


@api.get("/metrics")
async def metrics() -> dict:
    return {**await store.compute_metrics(), "whatsapp_provider": evolution_client.provider}


@api.post("/settings/trial-mode")
async def set_trial_mode(body: TrialModeRequest) -> dict:
    await store.set_trial_mode(body.trial_mode)
    return {"trial_mode": body.trial_mode}


app.include_router(api)
