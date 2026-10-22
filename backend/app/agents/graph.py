from __future__ import annotations
import re
from typing import Callable, Dict, List, Optional, TypedDict
from backend.app.models import BarbershopLead, LeadStatus
from backend.app.agents.objection_rag import (
    build_objection_response,
    detect_objections,
    normalize_text,
)


TRIAL_MODES = ("STAND_BY", "AUTO_LINK")

# Descadastro tem prioridade absoluta sobre qualquer outro sinal na mensagem.
UNSUBSCRIBE_PATTERNS = [
    re.compile(p)
    for p in (
        r"\b(para|pare|parar|parem) de (mandar|enviar|me mandar)",
        r"\bnao quero\b",
        r"\bnao tenho interesse\b",
        r"\bsair\b",
        r"\bme (tira|tire|remove|remova)\b",
        r"\bdescadastr",
        r"\bnao me (mande|mandem|chame|perturbe)",
        r"\bbloquear\b",
        r"\bstop\b",
    )
]

INTEREST_PATTERNS = [
    re.compile(p)
    for p in (
        r"\btenho interesse\b",
        r"\binteressad[oa]\b",
        r"\bquero\b",
        r"\bbora\b",
        r"\btopo\b",
        r"\bpode mandar\b",
        r"\bmanda\b",
        r"\bcomo funciona\b",
        r"\bquanto (custa|e|fica)\b",
        r"\btestar\b",
        r"^\s*(sim|claro|vamos)\b",
    )
]


class GraphState(TypedDict):
    """Estado que percorre os nós do grafo (compatível com o formato de estado do LangGraph)."""

    lead: BarbershopLead
    inbound_message: str
    trial_mode: str
    intent: Optional[str]
    objection_types: List[str]
    reply: Optional[str]
    langgraph_node: str


def classify_intent(message: str) -> str:
    """Nó 4.Router: UNSUBSCRIBE > OBJECTION_RAISED > INTERESTED > NEUTRAL."""
    text = normalize_text(message)
    if any(p.search(text) for p in UNSUBSCRIBE_PATTERNS):
        return "UNSUBSCRIBE"
    if detect_objections(text):
        return "OBJECTION_RAISED"
    if any(p.search(text) for p in INTEREST_PATTERNS):
        return "INTERESTED"
    return "NEUTRAL"


def _router_node(state: GraphState) -> GraphState:
    state["intent"] = classify_intent(state["inbound_message"])
    return state


def _unsubscribe_node(state: GraphState) -> GraphState:
    lead = state["lead"]
    lead.status = LeadStatus.DO_NOT_CONTACT
    state["langgraph_node"] = "4.Router:DoNotContact"
    state["reply"] = "Tranquilo, mestre. Não vou mais te mandar mensagem. Sucesso aí na barbearia!"
    return state


def _objection_node(state: GraphState) -> GraphState:
    lead = state["lead"]
    objections = detect_objections(state["inbound_message"])
    lead.status = LeadStatus.OBJECTION_RAISED
    lead.last_objection_type = objections[0]
    state["objection_types"] = objections
    state["langgraph_node"] = "4.ObjectionRAG"
    state["reply"] = build_objection_response(lead, objections, state["inbound_message"])
    return state


def _trial_activator_node(state: GraphState) -> GraphState:
    """Agente 5: chaveia entre Stand-by VIP (fila no Kanban) e Link Automático da vitrine /{slug}."""
    lead = state["lead"]
    if state["trial_mode"] == "AUTO_LINK":
        lead.status = LeadStatus.TRIAL_STARTED
        state["langgraph_node"] = "5.TrialActivator:AutoLink"
        state["reply"] = (
            f"Show! Sua página já tá no ar: {lead.preview_url}\n"
            "São 7 dias grátis, é só abrir e mandar o link pros seus clientes agendarem."
        )
    else:
        lead.status = LeadStatus.INTERESTED
        state["langgraph_node"] = "5.TrialActivator:StandBy"
        state["reply"] = (
            f"Show! Coloquei a {lead.shop_name} na fila VIP e sua vitrine tá sendo liberada.\n"
            "Se preferir, faço uma call rapidinha de 10 min aqui no WhatsApp pra te mostrar. Qual horário fica bom?"
        )
    return state


def _neutral_node(state: GraphState) -> GraphState:
    lead = state["lead"]
    state["langgraph_node"] = "4.Router:Neutral"
    state["reply"] = (
        f"Fechou! Se quiser, te mostro em 2 minutos como a {lead.shop_name} fica com agenda online "
        "e lembrete automático no Whats. Pode ser?"
    )
    return state


INTENT_EDGES: Dict[str, Callable[[GraphState], GraphState]] = {
    "UNSUBSCRIBE": _unsubscribe_node,
    "OBJECTION_RAISED": _objection_node,
    "INTERESTED": _trial_activator_node,
    "NEUTRAL": _neutral_node,
}


def classify_and_run_graph(
    lead: BarbershopLead, inbound_message: str, trial_mode: str = "STAND_BY"
) -> dict:
    """
    Executa o grafo determinístico Router -> (DoNotContact | ObjectionRAG | TrialActivator | Neutral)
    para a mensagem recebida. Altera o status do lead no lugar e devolve a resposta a enviar.
    Leads em DO_NOT_CONTACT nunca recebem resposta.
    """
    mode = trial_mode if trial_mode in TRIAL_MODES else "STAND_BY"
    state: GraphState = {
        "lead": lead,
        "inbound_message": inbound_message or "",
        "trial_mode": mode,
        "intent": None,
        "objection_types": [],
        "reply": None,
        "langgraph_node": lead.langgraph_node,
    }

    if lead.status == LeadStatus.DO_NOT_CONTACT:
        state["intent"] = "BLOCKED"
        state["langgraph_node"] = "4.Router:DoNotContact"
    else:
        state = _router_node(state)
        state = INTENT_EDGES[state["intent"]](state)

    lead.langgraph_node = state["langgraph_node"]
    return {
        "lead_id": lead.id,
        "intent": state["intent"],
        "objection_types": state["objection_types"],
        "reply": state["reply"],
        "new_status": lead.status.value,
        "langgraph_node": state["langgraph_node"],
        "trial_mode": mode,
    }
