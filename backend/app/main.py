from __future__ import annotations
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Dict, Literal, Optional, Tuple
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from backend.app.models import Interaction, LeadStatus
from backend.app.store import store
from backend.app.auth import AuthSettings, production_auth_problems, require_session, require_webhook_token
from backend.app.auth import router as auth_router
from backend.app.agents.scraper import ScraperError, active_lead_source, discover_barbershops
from backend.app.agents import apify_maps
from backend.app.services.lead_import import ImportFormatError, TEMPLATE_CSV, parse_leads_csv
from backend.app.agents.copywriter import generate_outbound_copy
from backend.app.agents.graph import ACTION_PROVISION_TRIAL, classify_and_run_graph
from backend.app.services.evolution import evolution_client
from backend.app.services.messaging import outbound_block_reason, send_to_lead
from backend.app.services.cadence_worker import CadenceConfig, CadenceWorker, schedule_after_touch, stop_cadence
from backend.app.services import barbershop_platform as platform_module
from backend.app.services import barbershop_webhook
from backend.app.services.trial_activation import (
    ProvisioningError,
    activate_trial,
    build_trial_message,
    trial_block_reason,
)


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


def integration_problems() -> list:
    """Integração com o barber-shop ligada exige segredo forte no webhook de volta."""
    if not platform_module.barbershop_platform.enabled:
        return []
    secret = barbershop_webhook.webhook_secret()
    return [] if len(secret) >= 32 else ["BARBERSHOP_WEBHOOK_SECRET ausente ou com menos de 32 caracteres"]


def validate_startup_config() -> None:
    """Com WhatsApp real, configuração de segurança fraca impede a API de subir (fail fast)."""
    problems = production_auth_problems(AuthSettings.from_env()) + integration_problems()
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


class ImportCsvRequest(BaseModel):
    csv: str = Field(min_length=1, max_length=1_100_000, description="Conteúdo do arquivo CSV")
    default_state: Optional[str] = Field(default=None, max_length=30, description="UF para linhas sem UF")
    default_city: Optional[str] = Field(default=None, max_length=80, description="Cidade para linhas sem cidade")


class TrialModeRequest(BaseModel):
    trial_mode: Literal["STAND_BY", "AUTO_LINK"]


EMAIL_REGEX = r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$"


class ActivateTrialRequest(BaseModel):
    email: Optional[str] = Field(default=None, max_length=254, pattern=EMAIL_REGEX)


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

    if result["action"] == ACTION_PROVISION_TRIAL:
        result["reply"], result["langgraph_node"] = await _provision_from_conversation(lead)
        await store.save_lead(lead)

    delivery = None
    if result["reply"]:
        delivery = await send_to_lead(lead, result["reply"], node=result["langgraph_node"], intent=result["intent"])
    return {"status": "processed", **result, "delivery": delivery, "lead": lead.to_dict()}


async def _provision_from_conversation(lead) -> Tuple[str, str]:
    """Link Automático: libera o teste na hora; se falhar, o lead vai para a fila de liberação manual."""
    try:
        trial = await activate_trial(lead)
        return build_trial_message(lead, trial), lead.langgraph_node
    except ProvisioningError as exc:
        logger.warning("Teste grátis de %s ficou pendente: %s", lead.shop_name, exc)
        lead.langgraph_node = "5.TrialActivator:PendingManual"
        reply = (
            f"Show, anotei o e-mail {lead.email}! Tô liberando a página da {lead.shop_name} "
            "e te mando o link aqui em instantes."
        )
        return reply, lead.langgraph_node


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


@app.post("/webhook/barbershop")
async def barbershop_webhook_receiver(request: Request) -> dict:
    """Ciclo de vida do teste grátis vindo do barber-shop (ativou, vai vencer, venceu, virou cliente)."""
    raw = await request.body()
    problem = barbershop_webhook.signature_problem(
        barbershop_webhook.webhook_secret(),
        request.headers.get("x-gerente-barber-timestamp"),
        request.headers.get("x-gerente-barber-signature"),
        raw,
    )
    if problem:
        raise HTTPException(status_code=401, detail=f"Webhook do barber-shop recusado: {problem}")

    try:
        payload = json.loads(raw)
        event, shop = payload["event"], payload["shop"]
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=400, detail="Corpo do webhook inválido")

    lead = await store.get_lead(str(shop.get("externalLeadId") or ""))
    if not lead:
        return {"status": "ignored", "reason": "lead não encontrado"}
    # Reenvio do mesmo aviso (o barber-shop tenta de novo se não recebeu 2xx): não repete a mensagem.
    if payload.get("id") and not await store.mark_event_processed(f"bs:{payload['id']}"):
        return {"status": "duplicate"}

    outcome = barbershop_webhook.apply_event(lead, event, shop)
    if not outcome.known:
        return {"status": "ignored", "reason": f"evento desconhecido: {event}"}
    await store.save_lead(lead)
    logger.info("barber-shop: %s para %s", event, lead.shop_name)

    # O aviso ao dono sai pela conversa do SDR (o barber-shop não manda o dele para leads do SDR).
    delivery = None
    if outcome.message:
        if lead.status == LeadStatus.DO_NOT_CONTACT:
            logger.warning("Aviso %s não enviado: %s pediu para não ser contatado", event, lead.shop_name)
        else:
            delivery = await send_to_lead(lead, outcome.message, node=lead.langgraph_node, intent="BILLING_NOTICE")
            if not delivery["delivered"]:
                logger.warning("Aviso %s para %s não saiu: %s", event, lead.shop_name, delivery["error"])
    return {"status": "processed", "event": event, "delivery": delivery, "lead": lead.to_dict()}


@api.post("/simulator/inbound")
async def simulate_inbound(body: SimulatedInbound) -> dict:
    """Simulador do painel. Só em modo MOCK: com WhatsApp real a resposta iria para o barbeiro de verdade."""
    if evolution_client.provider != "MOCK":
        raise HTTPException(status_code=403, detail="Simulador disponível só com WHATSAPP_PROVIDER=MOCK")
    return await handle_inbound(body.phone, body.message)


async def _save_found(found: list) -> dict:
    """Salva sem duplicar por telefone; `new` conta só quem ainda não estava no funil."""
    saved = []
    new = 0
    for lead in found:
        stored = await store.upsert_lead(lead)
        new += stored.id == lead.id
        saved.append(stored.to_dict())
    return {"status": "done", "count": len(saved), "new": new, "leads": saved}


@api.post("/leads/scrape")
async def scrape_leads(body: ScrapeRequest) -> dict:
    """
    Agente 1: descobre barbearias por UF/Cidade e salva (sem duplicar por telefone).
    Com o Apify a busca leva minutos: devolve `status: running` e o painel acompanha pelo `run_id`.
    """
    try:
        if active_lead_source() == "APIFY":
            run_id = await apify_maps.start_search(body.state, body.city, body.neighborhood)
            return {"status": "running", "run_id": run_id, "source": "APIFY"}
        found = await discover_barbershops(body.state, body.city, body.neighborhood)
    except ScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    return await _save_found(found)


@api.get("/leads/scrape/{run_id}")
async def scrape_progress(run_id: str, state: str, city: str, neighborhood: Optional[str] = None) -> dict:
    """Andamento de uma busca no Apify; quando termina, salva as barbearias encontradas."""
    if not apify_maps.is_valid_run_id(run_id):
        raise HTTPException(status_code=400, detail="Id de busca inválido")
    if not apify_maps.apify_token():
        raise HTTPException(status_code=409, detail="APIFY_TOKEN não configurado")
    params = ScrapeRequest(state=state, city=city, neighborhood=neighborhood)
    try:
        search = await apify_maps.fetch_search(run_id, params.state, params.city, params.neighborhood)
    except ScraperError as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    if search.status == "running":
        return {"status": "running", "run_id": run_id}
    return await _save_found(search.leads)


@api.post("/leads/import")
async def import_leads(body: ImportCsvRequest) -> dict:
    """Importa barbearias de uma planilha CSV (sem duplicar por telefone)."""
    try:
        parsed = parse_leads_csv(body.csv, body.default_state, body.default_city)
    except ImportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    result = await _save_found(parsed.leads)
    return {
        "imported": result["new"],
        "already_in_funnel": result["count"] - result["new"],
        "duplicates_in_file": parsed.duplicates_in_file,
        "rejected": [{"line": p.line, "reason": p.reason} for p in parsed.problems],
        "leads": result["leads"],
    }


@api.get("/leads/import/template")
async def import_template() -> dict:
    """Modelo de planilha, para o painel oferecer como download."""
    return {"filename": "modelo-barbearias.csv", "csv": TEMPLATE_CSV}


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


@api.post("/leads/{lead_id}/activate-trial")
async def activate_trial_endpoint(lead_id: str, body: ActivateTrialRequest) -> dict:
    """Stand-by VIP: um humano libera o teste pelo painel e o link vai pelo WhatsApp."""
    lead = await store.get_lead(lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead não encontrado")
    if body.email:
        lead.email = body.email.strip().lower()

    block_reason = trial_block_reason(lead)
    if block_reason:
        raise HTTPException(status_code=409, detail=block_reason)
    try:
        trial = await activate_trial(lead)
    except ProvisioningError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    stop_cadence(lead)
    await store.save_lead(lead)
    message = build_trial_message(lead, trial)
    delivery = await send_to_lead(lead, message, node=lead.langgraph_node, intent="TRIAL_ACTIVATED")
    return {"trial": {"url": trial.url, "created": trial.created}, "message": message, "delivery": delivery, "lead": lead.to_dict()}


@api.get("/metrics")
async def metrics() -> dict:
    return {
        **await store.compute_metrics(),
        "whatsapp_provider": evolution_client.provider,
        "barbershop_integration": platform_module.barbershop_platform.enabled,
        "lead_source": active_lead_source(),
    }


@api.post("/settings/trial-mode")
async def set_trial_mode(body: TrialModeRequest) -> dict:
    await store.set_trial_mode(body.trial_mode)
    return {"trial_mode": body.trial_mode}


app.include_router(api)
