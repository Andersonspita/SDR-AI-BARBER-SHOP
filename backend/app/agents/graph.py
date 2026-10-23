from __future__ import annotations
import re
from typing import Callable, Dict, List, Optional, TypedDict
from backend.app.models import BarbershopLead, LeadStatus, TrialStatus
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


EMAIL_PATTERN = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}", re.IGNORECASE)

# Ação que o grafo pede para a camada de I/O executar (o grafo em si não chama rede).
ACTION_PROVISION_TRIAL = "PROVISION_TRIAL"


def extract_email(message: str) -> Optional[str]:
    """Primeiro e-mail da mensagem, em minúsculas (login do dono no barber-shop)."""
    match = EMAIL_PATTERN.search(message or "")
    return match.group(0).lower().rstrip(".") if match else None


class GraphState(TypedDict):
    """Estado que percorre os nós do grafo (compatível com o formato de estado do LangGraph)."""

    lead: BarbershopLead
    inbound_message: str
    trial_mode: str
    intent: Optional[str]
    objection_types: List[str]
    reply: Optional[str]
    langgraph_node: str
    action: Optional[str]


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
    intent = classify_intent(state["inbound_message"])
    email = extract_email(state["inbound_message"]) if intent != "UNSUBSCRIBE" else None
    if email:
        state["lead"].email = email
        # Mandar o e-mail é topar o teste ("pode ser, meu e-mail é ...").
        if intent == "NEUTRAL":
            intent = "INTERESTED"
    state["intent"] = intent
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
    """
    Agente 5. O teste grátis vive no barber-shop e o login lá é por e-mail, então:
    sem e-mail -> pede o e-mail; Link Automático -> pede o provisionamento (ACTION_PROVISION_TRIAL);
    Stand-by VIP -> fila no Kanban para um humano liberar pelo painel.
    """
    lead = state["lead"]
    if lead.trial_shop_id:
        state["langgraph_node"] = "5.TrialActivator:AlreadyActive"
        state["reply"] = (
            f"Sua página da {lead.shop_name} já tá no ar: {lead.preview_url}\n"
            f"É só entrar com o e-mail {lead.email}. Qualquer dúvida, me chama aqui."
        )
    elif not lead.email:
        lead.status = LeadStatus.INTERESTED
        state["langgraph_node"] = "5.TrialActivator:AwaitingEmail"
        state["reply"] = (
            f"Show! Pra eu liberar a página da {lead.shop_name} com 7 dias grátis, "
            "me passa um e-mail seu? É ele que você vai usar pra entrar no painel."
        )
    elif state["trial_mode"] == "AUTO_LINK":
        lead.status = LeadStatus.INTERESTED
        state["langgraph_node"] = "5.TrialActivator:Provisioning"
        state["action"] = ACTION_PROVISION_TRIAL
        state["reply"] = None  # montada depois do provisionamento, com o link real
    else:
        lead.status = LeadStatus.INTERESTED
        state["langgraph_node"] = "5.TrialActivator:StandBy"
        state["reply"] = (
            f"Show! Coloquei a {lead.shop_name} na fila VIP e sua vitrine tá sendo liberada.\n"
            "Se preferir, faço uma call rapidinha de 10 min aqui no WhatsApp pra te mostrar. Qual horário fica bom?"
        )
    return state


# Situações em que o dono precisa pagar para a barbearia ficar (ou voltar) no ar.
OWES_PAYMENT = ("EXPIRED", "PAST_DUE", "SUSPENDED")


def _customer_node(state: GraphState) -> GraphState:
    """
    Quem já tem barbearia no barber-shop é cliente, não lead: a resposta depende da assinatura.
    Deve pagar -> manda a fatura (ou pede para alguém gerar a cobrança); em teste -> link para
    assinar; plano em dia -> atendimento.
    """
    lead = state["lead"]
    status = lead.subscription_status
    if status in OWES_PAYMENT:
        if lead.invoice_url:
            state["langgraph_node"] = "5.Billing:InvoiceSent"
            state["reply"] = (
                f"Aqui o link pra pagar: {lead.invoice_url}\n"
                f"Assim que o pagamento cair, a {lead.shop_name} fica em dia na hora."
            )
        else:
            # Sem fatura: a cobrança é gerada na plataforma (precisa do CPF/CNPJ); o painel sinaliza.
            lead.needs_billing_help = True
            state["langgraph_node"] = "5.Billing:NeedsCharge"
            state["reply"] = (
                f"Show! Vou gerar a cobrança da {lead.shop_name} e te mando o link aqui em instantes. "
                "Se for no CNPJ da barbearia, já me passa o número."
            )
    elif (
        status in (None, "TRIAL")
        and state["intent"] == "INTERESTED"
        and lead.subscribe_url
        # Antes do 1º acesso, "quero" é pedir o acesso de novo, não assinar.
        and lead.trial_status in (TrialStatus.ACTIVATED, TrialStatus.EXPIRING)
    ):
        state["langgraph_node"] = "5.Billing:SubscribeLink"
        state["reply"] = (
            f"Pra assinar é rapidinho: {lead.subscribe_url}\n"
            "Escolhe o plano e paga por Pix, boleto ou cartão. Se travar em algo, me chama aqui."
        )
    elif status in (None, "TRIAL") and state["intent"] == "INTERESTED":
        return _trial_activator_node(state)
    else:
        state["langgraph_node"] = "5.Billing:Support"
        state["reply"] = (
            f"Fechou! Qualquer dúvida sobre a página da {lead.shop_name} ({lead.preview_url}), "
            "é só me chamar aqui."
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
        "action": None,
    }

    if lead.status == LeadStatus.DO_NOT_CONTACT:
        state["intent"] = "BLOCKED"
        state["langgraph_node"] = "4.Router:DoNotContact"
    else:
        state = _router_node(state)
        if lead.trial_shop_id and state["intent"] in ("INTERESTED", "NEUTRAL"):
            state = _customer_node(state)
        else:
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
        "action": state["action"],
    }
