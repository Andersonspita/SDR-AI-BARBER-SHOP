# Integração SDR-AI ↔ barber-shop (teste grátis)

Contrato entre o SDR-AI (este repositório) e o [barber-shop](https://github.com/Andersonspita/barber-shop).
Os dois lados precisam usar os mesmos segredos e a mesma URL pública.

## Configuração

| SDR-AI | barber-shop | Observação |
|---|---|---|
| `BARBERSHOP_API_URL` | — | URL da API do barber-shop (no deploy com nginx, a do site) |
| `BARBERSHOP_PROVISIONING_KEY` | `PROVISIONING_API_KEY` | Mesmo valor. Só cria testes; não é a `PLATFORM_ADMIN_KEY` |
| `BARBERSHOP_WEBHOOK_SECRET` | `SDR_WEBHOOK_SECRET` | Mesmo valor, 32+ caracteres |
| — | `SDR_WEBHOOK_URL` | `https://<sdr>/webhook/barbershop` |
| `SAAS_BASE_URL` | `APP_URL` | Mesmo valor: as vitrines ficam em `/{slug}` |
| `TRIAL_DAYS` | — | Duração do teste (1 a 30, padrão 7) |
| — | `TRIAL_SETUP_LINK_TTL_HOURS` | Validade do link de primeiro acesso (padrão 72) |

## 1. Provisionamento: SDR → barber-shop

`POST {BARBERSHOP_API_URL}/provisioning/trials` com o cabeçalho `X-Provisioning-Key: <chave>`.

```json
{
  "externalLeadId": "id do lead no SDR",
  "shopName": "Barbearia São José & Cia",
  "slug": "barbearia-sao-jose-cia",
  "city": "Vitória da Conquista",
  "plan": "SOLO | SQUAD | PRO | EMPIRE",
  "trialDays": 7,
  "adminName": "Marcos Oliveira",
  "adminEmail": "dono@email.com",
  "adminPhone": "5577999123456"
}
```

As respostas possíveis:

- **`201 Created`** quando a barbearia foi criada agora.
- **`200 OK`** quando o lead já tinha barbearia. O pedido é idempotente por `externalLeadId`.

```json
{
  "created": true,
  "shop": {
    "id": "uuid",
    "slug": "barbearia-sao-jose-cia-2",
    "name": "Barbearia São José & Cia",
    "url": "https://barber-shop.horizonaj.com.br/barbearia-sao-jose-cia-2",
    "subscriptionStatus": "TRIAL",
    "plan": "SQUAD",
    "trialEndsAt": "2026-10-29T13:00:00.000Z"
  },
  "admin": { "id": "uuid", "email": "dono@email.com" },
  "setupUrl": "https://.../barbearia-sao-jose-cia-2/login/recuperar?token=..."
}
```

- Se o slug pedido estiver em uso, o barber-shop tenta `-2` até `-9`. O SDR adota o slug devolvido.
- `setupUrl` é um link de uso único para o dono definir a senha. Ele vem `null` quando o dono já entrou. Um pedido repetido antes do primeiro acesso gera um link novo.
- A barbearia nasce com o dono (admin), a jornada seg–sex 9h–18h e sáb 9h–14h, e os serviços Corte, Barba e Corte + Barba.
- Erros: `403` para chave errada, `404` quando a rota está desligada (sem `PROVISIONING_API_KEY`), `400` para validação (`{"message": [...]}`) e `409` quando não há endereço livre.

## 2. Ciclo de vida: barber-shop → SDR

`POST {SDR_WEBHOOK_URL}`, entregue por fila (BullMQ) com até 8 tentativas e espera exponencial.

Cabeçalhos:

- `X-Gerente-Barber-Event`: nome do evento
- `X-Gerente-Barber-Timestamp`: segundos Unix
- `X-Gerente-Barber-Signature`: `sha256=` seguido de `hex(HMAC_SHA256(segredo, "{timestamp}.{corpo bruto}"))`

O SDR recusa assinaturas inválidas e avisos com mais de 5 minutos (proteção contra reenvio). Cada `id` é registrado: um aviso reenviado responde `duplicate` e não repete a mensagem ao dono.

```json
{
  "id": "uuid do aviso",
  "event": "trial.activated",
  "occurredAt": "2026-10-22T13:00:00.000Z",
  "shop": {
    "id": "uuid",
    "slug": "barbearia-sao-jose-cia-2",
    "externalLeadId": "id do lead",
    "subscriptionStatus": "TRIAL",
    "plan": "SQUAD",
    "trialEndsAt": "2026-10-29T13:00:00.000Z",
    "name": "Barbearia São José & Cia",
    "url": "https://barber-shop.horizonaj.com.br/barbearia-sao-jose-cia-2",
    "subscribeUrl": "https://barber-shop.horizonaj.com.br/barbearia-sao-jose-cia-2/dashboard/assinatura",
    "invoiceUrl": "https://www.asaas.com/i/... (ou null)",
    "suspendsAt": "2026-10-28T15:00:00.000Z (ou null)"
  }
}
```

| Evento | Quando | Efeito no SDR | Mensagem ao dono (pela conversa do SDR) |
|---|---|---|---|
| `trial.activated` | O dono definiu a senha ou fez o primeiro login | `trial_status = ACTIVATED` | Três mensagens: primeiros passos + manual em PDF (`BARBERSHOP_MANUAL_URL`, padrão `SAAS_BASE_URL/manual-da-barbearia.pdf`); como pôr o link no Instagram, WhatsApp Business e Google; e o texto pronto para o dono encaminhar aos clientes |
| `trial.expiring` | Faltam 48h para o fim do teste | `trial_status = EXPIRING` | Dia do fim + como pagar a 1ª mensalidade (`invoiceUrl` ou `subscribeUrl`) |
| `subscription.invoice_created` | Fatura gerada (sozinha, dias antes do vencimento, ou pelo operador) | fatura salva no lead | Link de pagamento do Asaas, ou a tela de Assinatura do painel |
| `trial.expired` | A 1ª fatura (que vence no fim do teste) ficou em aberto | `trial_status = EXPIRED`, `subscription_status = PAST_DUE` | "Terminou e a 1ª mensalidade venceu" + como pagar + quando o agendamento pausa (`suspendsAt`) |
| `subscription.activated` | 1º pagamento (Asaas ou baixa manual) | `trial_status = CONVERTED` e lead `CONVERTED` | "Pagamento confirmado" |
| `subscription.past_due` | Mensalidade seguinte vencida | `subscription_status = PAST_DUE` | Como pagar + quando o agendamento pausa (`suspendsAt`) |
| `subscription.suspended` | Passou da tolerância sem pagar | `BLOCKED` | "Agendamento online pausado; página, agenda e painel seguem" + como pagar |
| `subscription.reactivated` | Pagou o atraso | `ACTIVE` | "Em dia, agendamento liberado" |

A barbearia **nunca sai do ar** por atraso: o barber-shop só pausa o agendamento online dos clientes. `subscriptionStatus` chega como `TRIAL`, `ACTIVE`, `PAST_DUE`, `BLOCKED` ou `EXEMPT` (cortesia); `SUSPENDED` e `EXPIRED` só aparecem em leads de antes da cobrança unificada. `trialEndsAt` e `suspendsAt` são dias (meia-noite UTC) e as mensagens mostram só o dia.

Só barbearias que vieram do SDR (com `externalLeadId`) geram avisos. **Para essas, o barber-shop não manda os próprios avisos pelo WhatsApp da plataforma**: o SDR manda na conversa que já tem com o dono, e a resposta dele cai no grafo do SDR. Leads descadastrados (`DO_NOT_CONTACT`) têm a situação atualizada, mas não recebem mensagem.

### Respostas do dono que já é cliente

Para quem já tem barbearia (`trial_shop_id`), interesse ou resposta neutra não recebe mais o discurso de venda:

- **Deve pagar** (mensalidade vencida ou agendamento pausado): com link de pagamento, o SDR manda o link. Sem link (pagamento online ainda não ligado), responde "vou gerar a cobrança" e marca `needs_billing_help`. O painel mostra o alerta; em `/plataforma` → **Assinatura**, ligue o pagamento online com o CPF/CNPJ do dono (o link chega na conversa sozinho) ou dê a baixa manual se ele pagou por fora.
- **Em teste, já usando o painel:** link para assinar (`subscribeUrl`).
- **Em dia:** atendimento ("qualquer dúvida, me chama").

## Fluxo na conversa

```
interesse ──► sem e-mail? ──► "me passa um e-mail seu?"
                  │
                  ▼ (e-mail recebido)
     Link Automático ──► POST /provisioning/trials ──► responde com a vitrine + o link de senha
     Stand-by VIP    ──► fila no painel ──► humano clica em "Liberar e enviar link"
     falha no barber-shop ──► fila manual + "te mando o link em instantes"
```
