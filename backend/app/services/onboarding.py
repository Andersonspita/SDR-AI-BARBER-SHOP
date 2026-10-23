"""
Boas-vindas ao dono que topou o teste grátis: o que ele recebe para começar a usar e divulgar.

- Ao liberar o teste (`trial_message`): o link da vitrine, que é o que ele vai divulgar, e o link
  para criar a senha.
- No primeiro acesso ao painel (evento `trial.activated` do barber-shop, `welcome_messages`): os
  primeiros passos com o manual em PDF, como colocar o link no Instagram, no WhatsApp e no Google, e
  uma mensagem pronta, separada, para ele segurar e encaminhar aos clientes.
"""
from __future__ import annotations
import os
from typing import List, Optional
from backend.app.models import SAAS_BASE_URL


def manual_url() -> str:
    """PDF do manual da barbearia, publicado pelo barber-shop."""
    custom = os.getenv("BARBERSHOP_MANUAL_URL", "").strip()
    return custom or f"{SAAS_BASE_URL.rstrip('/')}/manual-da-barbearia.pdf"


def trial_message(shop_name: str, shop_url: str, setup_url: Optional[str], email: Optional[str], trial_days: int) -> str:
    if setup_url:
        return (
            f"Prontinho! A página da {shop_name} já tá no ar: {shop_url}\n"
            "Esse é o link que seus clientes vão usar pra marcar horário.\n"
            f"Pra entrar no painel, crie sua senha por aqui (vale por 72h): {setup_url}\n"
            f"São {trial_days} dias grátis, já com Corte, Barba e Combo cadastrados. "
            "Assim que você entrar, te mando o passo a passo e o manual."
        )
    return (
        f"Sua página da {shop_name} tá no ar: {shop_url}\n"
        f"É só entrar com o e-mail {email}. Se esqueceu a senha, use o \"esqueci minha senha\" na tela de login."
    )


def welcome_messages(shop_name: str, shop_url: str) -> List[str]:
    """Três mensagens, nesta ordem. A última é só o texto para os clientes, pronto para encaminhar."""
    return [
        (
            f"Bem-vindo ao Gerente Barber! ✅ O painel da {shop_name} tá liberado.\n"
            "Pra deixar tudo pronto em 5 minutos:\n"
            "1. Em Barbearia → Serviços, ajuste preços e serviços (Corte, Barba e Combo já vieram cadastrados)\n"
            "2. Em Equipe → Jornada, confira os dias e horários de atendimento\n"
            f"3. Divulgue o seu link de agendamento: {shop_url}\n"
            f"Manual completo (PDF): {manual_url()}"
        ),
        (
            "Como colocar o link pros clientes acharem:\n"
            "📸 Instagram: Editar perfil → Links → Adicionar link externo\n"
            "💬 WhatsApp Business: Ferramentas comerciais → Perfil comercial → Site. Ligue também a "
            "Mensagem de saudação e a de ausência com o link\n"
            "📍 Google Maps: no perfil da empresa, coloque o link como site ou link de agendamento\n"
            "Abaixo vai uma mensagem pronta: é só segurar nela e tocar em Encaminhar pros seus clientes 👇"
        ),
        (
            f"Agora a {shop_name} tem agendamento online! ✂️\n"
            f"Escolha o serviço, o barbeiro e o horário por aqui: {shop_url}\n"
            "Você recebe a confirmação e um lembrete no WhatsApp."
        ),
    ]
