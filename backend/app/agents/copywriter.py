from __future__ import annotations
import hashlib
from backend.app.models import BarbershopLead, TechMaturity


FEW_SHOT_PROMPTS = {
    "system_instruction": (
        "Você é o Agente Copywriter do Gerente Barber (SaaS de gestão para barbearias). "
        "Escreva mensagens de WhatsApp diretas, informais e respeitosas (máx 4 a 5 linhas). "
        "PROIBIDO usar jargões corporativos como 'sinergia', 'ROI', 'otimização de processos' ou 'B2B'."
    ),
    "examples": [
        {
            "profile": "Solo (1 barbeiro) + WhatsApp Manual na Bio",
            "output": (
                "Fala mestre! Vi no Insta da {shop_name} que você agenda direto pelo Whats.\n"
                "Quanto tempo você perde por dia respondendo cliente enquanto corta cabelo?\n"
                "No Gerente Barber o cliente agenda em 2 toques e recebe lembrete automático pra não faltar."
            ),
        },
        {
            "profile": "Equipe (3+ barbeiros) + Fechamento de Comissão",
            "output": (
                "Opa irmão, tudo certo na {shop_name}?\n"
                "No fim do mês, quanto tempo você gasta fechando a comissão de cada barbeiro na ponta do lápis?\n"
                "O Gerente Barber já separa comissão e repasse automático sem dor de cabeça."
            ),
        },
        {
            "profile": "Usa Concorrente (AppBarber / Booksy / Trinks / Gendo)",
            "output": (
                "Fala parceiro da {shop_name}! Vi que você usa o {competitor}.\n"
                "Conseguimos reduzir sua mensalidade e entregar lembrete via WhatsApp que não deixa o cliente faltar.\n"
                "Topa comparar rapidinho sem compromisso?"
            ),
        },
    ],
}

GREETINGS_SPINTAX = [
    "Fala mestre",
    "Opa irmão",
    "Fala parceiro",
    "Salve mestre",
]


def _pick_spintax(seed_key: str, options: list[str]) -> str:
    idx = int(hashlib.md5(seed_key.encode()).hexdigest()[:6], 16) % len(options)
    return options[idx]


def generate_outbound_copy(lead: BarbershopLead, touchpoint: str = "D0") -> str:
    """
    Agente 2: Gera copy personalizada com spintax dinâmico baseada em Few-Shot Prompts,
    variando conforme porte (Solo vs Equipe) e maturidade tecnológica (Manual vs Concorrente).
    """
    greeting = _pick_spintax(f"{lead.id}:{touchpoint}", GREETINGS_SPINTAX)
    tp = (touchpoint or "D0").upper()

    if tp == "D2":
        return (
            f"{greeting}! Passando rapidinho pra te mostrar como fica o painel da {lead.shop_name} "
            f"com os horários organizados e confirmação automática no Whats.\n"
            f"Seus clientes marcam em 2 toques sem baixar app nenhum. Quer que eu ative pra você ver?"
        )

    if tp == "D5":
        return (
            f"{greeting}! Deixei uma página da {lead.shop_name} pré-configurada no Gerente Barber "
            f"com 7 dias de teste grátis.\n"
            f"Já tá com o nome da sua barbearia prontinho pra testar hoje sem compromisso. Posso te mandar o acesso?"
        )

    # Touchpoint D0
    if lead.current_system == TechMaturity.COMPETITOR and lead.competitor_name:
        return (
            f"{greeting}, tudo certo na {lead.shop_name}?\n"
            f"Vi que você usa o {lead.competitor_name}. Conseguimos reduzir sua mensalidade e entregar "
            f"lembrete via WhatsApp que não deixa o cliente faltar.\n"
            f"Bora fazer um teste grátis de 7 dias pra comparar?"
        )

    if lead.estimated_barbers >= 3:
        return (
            f"{greeting}, tudo certo na {lead.shop_name}?\n"
            f"No fim do mês, quanto tempo você gasta fechando a comissão de cada barbeiro na ponta do lápis?\n"
            f"No Gerente Barber o cálculo de comissão e lembrete anti-falta no Whats rodam 100% no automático."
        )

    # Default / Solo / WhatsApp Manual
    return (
        f"{greeting}! Vi no perfil da {lead.shop_name} que você agenda direto pelo Whats.\n"
        f"Quanto tempo você perde por dia respondendo cliente enquanto corta cabelo?\n"
        f"Com o Gerente Barber ({lead.recommended_plan.get('monthly_anchor', 'R$ 49,90')}/mês) o cliente agenda sozinho 24h e confirma no Whats."
    )
