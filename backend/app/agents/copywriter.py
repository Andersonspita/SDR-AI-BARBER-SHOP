from __future__ import annotations
import hashlib
import os
from backend.app.models import BarbershopLead, LeadSource, TechMaturity


# Regras da prospecção fria pelo WhatsApp. Número desconhecido falando de vendas é denunciado
# fácil, e denúncia derruba o número; por isso toda mensagem:
#   1. diz quem fala (SDR_SENDER_NAME) e só afirma o que o SDR realmente sabe do lead
#      (de onde ele veio e o que o link do site/bio mostrou);
#   2. promete só o que o Gerente Barber entrega hoje;
#   3. fecha com uma pergunta de sim ou não e uma saída educada ("me avisa que eu não mando mais"),
#      que o grafo entende como descadastro.
FEW_SHOT_PROMPTS = {
    "system_instruction": (
        "Você é o Agente Copywriter do Gerente Barber (agendamento online para barbearias). "
        "Escreva mensagens de WhatsApp diretas, informais e respeitosas (no máximo 4 linhas). "
        "Diga quem está falando, não invente o que viu do lead, termine com uma pergunta de sim ou não "
        "e ofereça parar de mandar mensagem. PROIBIDO usar jargões como 'sinergia', 'ROI' ou 'B2B'."
    ),
    "examples": [
        {
            "profile": "Achada no Google Maps, agenda pelo WhatsApp (link wa.me no site/bio)",
            "output": (
                "Salve, mestre! Aqui é o Anderson, do Gerente Barber. Achei a {shop_name} no Google Maps.\n"
                "Vi que o agendamento de vocês é pelo WhatsApp. Com o Gerente Barber o cliente marca sozinho "
                "pelo link, 24h, e recebe confirmação e lembrete no Whats.\n"
                "Posso deixar uma página com o nome da {shop_name} pra você testar 7 dias grátis? "
                "Se não fizer sentido, me avisa que eu não mando mais."
            ),
        },
        {
            "profile": "Usa concorrente (AppBarber / Booksy / Trinks / Gendo)",
            "output": (
                "Fala, parceiro! Aqui é o Anderson, do Gerente Barber. Achei a {shop_name} no Google Maps.\n"
                "Vi que vocês agendam pelo {competitor}. No Gerente Barber o lembrete no WhatsApp já vem "
                "incluso e os planos começam em R$ 35/mês.\n"
                "Topa comparar com 7 dias grátis? Se não fizer sentido, me avisa que eu não mando mais."
            ),
        },
    ],
}

OPENERS = ["Salve, mestre!", "Fala, mestre!", "Opa, tudo certo?", "Fala, parceiro!"]
OPT_OUT = "Se não fizer sentido, me avisa que eu não mando mais."
SOURCES_FROM_MAPS = (LeadSource.GOOGLE_PLACES, LeadSource.APIFY_GOOGLE_MAPS)


def _pick_spintax(seed_key: str, options: list[str]) -> str:
    idx = int(hashlib.md5(seed_key.encode()).hexdigest()[:6], 16) % len(options)
    return options[idx]


def _sender() -> str:
    """"Aqui é o Anderson, do Gerente Barber." (SDR_SENDER_NAME) ou só "Aqui é do Gerente Barber."."""
    name = os.getenv("SDR_SENDER_NAME", "").strip()
    return f"Aqui é o {name}, do Gerente Barber." if name else "Aqui é do Gerente Barber."


def _how_we_found(lead: BarbershopLead) -> str:
    """De onde o lead veio, sem inventar: do mapa, dizemos; de planilha, só a cidade."""
    if lead.source in SOURCES_FROM_MAPS:
        return f"Achei a {lead.shop_name} no Google Maps."
    if lead.city:
        return f"Tô falando com algumas barbearias de {lead.city}."
    return ""


def _pitch(lead: BarbershopLead) -> str:
    """O que o produto resolve, a partir do que o link do site/bio realmente mostrou."""
    if lead.current_system == TechMaturity.COMPETITOR and lead.competitor_name:
        return (
            f"Vi que vocês agendam pelo {lead.competitor_name}. No Gerente Barber o lembrete no WhatsApp "
            f"já vem incluso e os planos começam em {lead.recommended_plan.get('monthly_anchor', 'R$ 35,00')}/mês."
        )
    if lead.estimated_barbers >= 3:
        return (
            "Com o Gerente Barber cada barbeiro tem a própria agenda, o cliente marca sozinho pelo link "
            "e a comissão de cada um sai calculada no financeiro."
        )
    observed = "Vi que o agendamento de vocês é pelo WhatsApp. " if lead.current_system == TechMaturity.MANUAL_WHATSAPP else ""
    return (
        f"{observed}Com o Gerente Barber o cliente marca sozinho pelo link, 24h, e recebe confirmação e "
        "lembrete no Whats. Você para de responder mensagem no meio do corte."
    )


def generate_outbound_copy(lead: BarbershopLead, touchpoint: str = "D0") -> str:
    """
    Agente 2: mensagem da cadência (D0, D2, D5). Varia a saudação (spintax estável por lead) e o
    argumento conforme o que se sabe do lead; nunca afirma algo que o SDR não viu.
    """
    opener = _pick_spintax(f"{lead.id}:{touchpoint}", OPENERS)
    tp = (touchpoint or "D0").upper()

    if tp == "D2":
        return (
            f"{opener} Passando de novo rapidinho: na página da {lead.shop_name} o cliente escolhe serviço, "
            "barbeiro e horário sozinho, sem baixar app, e você só acompanha a agenda.\n"
            f"Quer testar 7 dias grátis? {OPT_OUT}"
        )

    if tp == "D5":
        return (
            f"{opener} Última mensagem, prometo: a página da {lead.shop_name} no Gerente Barber fica pronta "
            "hoje, já com os serviços cadastrados, pra testar 7 dias grátis e sem compromisso.\n"
            "Se quiser, é só responder \"quero\". Se não, fica o contato e boas vendas por aí! ✂️"
        )

    intro = " ".join(part for part in (opener, _sender(), _how_we_found(lead)) if part)
    if lead.current_system == TechMaturity.COMPETITOR and lead.competitor_name:
        question = f"Topa comparar com 7 dias grátis? {OPT_OUT}"
    else:
        question = f"Posso deixar uma página com o nome da {lead.shop_name} pra você testar 7 dias grátis? {OPT_OUT}"
    return f"{intro}\n{_pitch(lead)}\n{question}"
