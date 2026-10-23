from __future__ import annotations
import re
import unicodedata
from typing import List, Optional
from backend.app.models import BarbershopLead
from backend.app.agents.scraper import COMPETITOR_SIGNATURES


def normalize_text(text: str) -> str:
    """Minúsculas e sem acentos para casar padrões independente da grafia do barbeiro."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return normalized.encode("ascii", "ignore").decode("ascii").lower()


# Base de conhecimento do Agente 4 (RAG local): padrões de detecção + resposta especialista.
# A ordem define a ordem em que as respostas aparecem quando há múltiplas objeções.
OBJECTION_KNOWLEDGE_BASE = [
    {
        "type": "SEM_TEMPO",
        "patterns": [
            r"nao tenho tempo",
            r"sem tempo",
            r"\bcadastrar\b",
            r"trabalho (de|pra|para) (cadastrar|configurar)",
        ],
        "response": (
            "Cadastro não é problema: nossa equipe importa seus clientes e horários "
            "em uns 10 minutinhos, você não precisa digitar nada."
        ),
    },
    {
        "type": "CLIENTES_VELHOS",
        "patterns": [
            r"clientes? (sao|e|é)? ?(mais )?(velhos|idosos|antigos)",
            r"clientes? velhos",
            r"so (ligam|liga|usam ligacao|usa ligacao)",
            r"nao sabem? mexer",
            r"publico (mais )?velho",
        ],
        "response": (
            "Pros clientes mais velhos funciona igual: a confirmação e o lembrete chegam no "
            "WhatsApp, sem baixar app. E quem prefere ligar, você marca na agenda em segundos."
        ),
    },
    {
        "type": "CADERNO",
        "patterns": [
            r"caderno",
            r"agenda de papel",
            r"\bpapel\b",
            r"\banoto\b",
        ],
        "response": (
            "O caderno funciona até alguém esquecer: barbearia perde em média de "
            "R$ 600 a R$ 1.200 por mês com falta e horário esquecido."
        ),
    },
    {
        "type": "CONCORRENTE",
        "patterns": [
            r"concorrente",
            r"outro (sistema|app|aplicativo)",
            *[rf"\b{re.escape(sig)}\b" for sig in COMPETITOR_SIGNATURES],
        ],
        "response": (
            "Dá pra comparar com o {competitor} sem risco: são 7 dias grátis, o lembrete no "
            "WhatsApp já vem incluso e os planos começam em R$ 35/mês."
        ),
    },
]

_COMPILED_KB = [
    (entry["type"], [re.compile(p) for p in entry["patterns"]], entry["response"])
    for entry in OBJECTION_KNOWLEDGE_BASE
]


def detect_objections(message: str) -> List[str]:
    """Agente 4: retorna todas as objeções clássicas presentes na mensagem (pode haver mais de uma)."""
    text = normalize_text(message)
    return [
        objection_type
        for objection_type, patterns, _ in _COMPILED_KB
        if any(p.search(text) for p in patterns)
    ]


def detect_competitor_name(message: str) -> Optional[str]:
    text = normalize_text(message)
    for signature, name in COMPETITOR_SIGNATURES.items():
        if re.search(rf"\b{re.escape(signature)}\b", text):
            return name
    return None


def build_objection_response(lead: BarbershopLead, objection_types: List[str], message: str = "") -> str:
    """
    Agente 4: monta a resposta empática combinando os trechos da base de conhecimento
    e sempre fecha no agendamento sem baixar aplicativo.
    """
    competitor = detect_competitor_name(message) or lead.competitor_name or "sistema que você usa hoje"
    snippets = [
        response.format(competitor=competitor)
        for objection_type, _, response in _COMPILED_KB
        if objection_type in objection_types
    ]

    opener = "Entendo total, mestre, faz sentido sua preocupação." if len(snippets) > 1 else "Entendo, mestre."
    closing = (
        f"E o cliente agenda sem baixar app nenhum, só pelo link da {lead.shop_name} no Whats. "
        "Quer ver como fica?"
    )
    return "\n".join([opener, *snippets, closing])
