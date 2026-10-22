# Gerente Barber • SDR-AI

Sistema autônomo de prospecção outbound para o SaaS **Gerente Barber** (`https://barber-shop.horizonaj.com.br`).
Descobre barbearias por UF/cidade, aborda o dono no WhatsApp, rebate objeções e entrega a vitrine `/{slug}` com 7 dias grátis.

Roda 100% local sem chaves de API: o scraper usa um gerador realista e o WhatsApp fica em modo `MOCK`.
Leads desse gerador são marcados como **fictícios** e nunca recebem WhatsApp real.

## Arquitetura

| Agente | Arquivo | Função |
|---|---|---|
| 1. Scraper & Bio Detector | `backend/app/agents/scraper.py` | Busca por UF/Cidade/Bairro e classifica a bio (`wa.me` → ALTA, concorrente → MIGRAÇÃO) |
| 2. Copywriter Few-Shot | `backend/app/agents/copywriter.py` | Gancho por porte (Solo × Equipe) e maturidade (Manual × Concorrente), com spintax |
| 3. Cadência Anti-Ban | `backend/app/services/cadence_worker.py` | Worker que envia D2/D5 sozinho, com delay de 45–180 s, janela de horário e limite diário |
| 4. RAG de Objeções | `backend/app/agents/objection_rag.py` | 4 objeções clássicas, inclusive várias na mesma mensagem |
| 5. Trial Activator | `backend/app/agents/graph.py` | Grafo de estados: descadastro > objeção > interesse; modo **Stand-by VIP** ou **Link Automático** |

- **API:** FastAPI em `backend/app/main.py`.
- **Persistência:** `backend/app/store.py` escolhe o repositório na inicialização: PostgreSQL (`backend/app/repositories/postgres.py`) quando `DATABASE_URL` conecta, ou memória (`repositories/memory.py`) como fallback. O schema `database/init.sql` é idempotente e roda tanto no initdb do Docker quanto a cada subida da API, aplicando colunas novas em bancos antigos.
- **WhatsApp:** `backend/app/services/evolution.py` (Evolution API; com `WHATSAPP_PROVIDER=MOCK`, as mensagens vão só para o log). Todo envio passa por `backend/app/services/messaging.py`, que aplica a trava de leads fictícios.
- **Autenticação:** `backend/app/auth.py`. Login de admin único (env) com sessão JWT em cookie httpOnly; o webhook exige `WEBHOOK_SECRET`.
- **Painel:** Next.js 14 em `frontend/`, com o design system do `/barbearia-elite` (Archivo + Public Sans).

## Rodando localmente

Pré-requisitos: Python 3.11+ e Node 20+.

```bash
python -m venv .venv
```

```bash
.venv/Scripts/pip install -r backend/requirements.txt
```

(No Linux/macOS, troque `.venv/Scripts/` por `.venv/bin/`.)

Suba a API **a partir da raiz do projeto** (os imports são `backend.app.*`):

```bash
.venv/Scripts/python -m uvicorn backend.app.main:app --reload --port 8000
```

Em outro terminal, suba o painel:

```bash
cd frontend && npm install && npm run dev
```

Acesse `http://localhost:3000` e entre com `ADMIN_EMAIL`/`ADMIN_PASSWORD` (em dev, os valores do `.env.example`). Se o painel rodar em outra porta, inclua a origem dele em `FRONTEND_ORIGINS` na API.

Sem `DATABASE_URL`, a API roda em memória. Para persistir localmente, suba só o banco e exporte a URL antes do uvicorn:

```bash
docker compose up -d postgres
```

```bash
export DATABASE_URL=postgresql://sdr_admin:sdr_secret_2026@localhost:5432/gerente_barber_sdr
```

Confira o backend ativo em `GET /health` (`"storage": "postgres"` ou `"memory"`).

### Testes

```bash
.venv/Scripts/python -m pytest backend/tests -q
```

Os testes de Postgres são pulados por padrão. Para rodá-los, aponte `TEST_DATABASE_URL` para um banco **descartável** (as tabelas são truncadas):

```bash
docker run -d --name sdr_ai_pg_test -e POSTGRES_USER=sdr_test -e POSTGRES_PASSWORD=sdr_test -e POSTGRES_DB=sdr_test -p 45432:5432 postgres:16-alpine
```

```bash
TEST_DATABASE_URL=postgresql://sdr_test:sdr_test@localhost:45432/sdr_test .venv/Scripts/python -m pytest backend/tests -q
```

```bash
cd frontend && npm run typecheck && npm run lint
```

## Rodando com Docker

```bash
cp .env.example .env
```

```bash
docker compose up --build
```

Serviços: painel `:3000`, API `:8000`, Postgres `:5432` e Redis `:6379`. A API espera o Postgres ficar saudável e grava nele (`GET /health` → `"storage": "postgres"`).

Se a porta 3000 já estiver em uso, troque a do painel (o CORS da API acompanha):

```bash
FRONTEND_PORT=3001 docker compose up -d --build
```

A Evolution API (`evoapicloud/evolution-api`, `:8080`) só sobe com o profile `whatsapp`, porque no modo padrão (`WHATSAPP_PROVIDER=MOCK`) ela não é usada. Ela grava no schema `evolution_api` do mesmo Postgres:

```bash
docker compose --profile whatsapp up -d
```

O `NEXT_PUBLIC_API_URL` é embutido no build do painel: se mudar, rode o build de novo.

## Segurança

- **Painel e `/api/*`:** exigem sessão. `POST /api/auth/login` grava o cookie `sdr_session` (httpOnly, SameSite=Lax) e também devolve o token para scripts (`Authorization: Bearer <token>`). Cinco senhas erradas em 5 min bloqueiam o IP temporariamente.
- **Webhook:** `POST /webhook/whatsapp` só aceita o `WEBHOOK_SECRET` no header `X-Webhook-Token` ou em `?token=`. O compose já configura a Evolution com o token na URL.
- **Fail fast:** com `WHATSAPP_PROVIDER` real, a API não sobe se a senha, os segredos ou o `COOKIE_SECURE` estiverem fracos (veja os comentários do `.env.example`). Em `MOCK`, isso aparece só como aviso no log.
- **Leads fictícios:** sem `GOOGLE_MAPS_API_KEY`, os leads saem com `source=SYNTHETIC`. Com WhatsApp real, qualquer envio para eles é bloqueado (D0 responde `409`, o worker tira o lead da régua e o simulador do painel fica desligado). Leads antigos do banco foram migrados como fictícios.

## Cadência automática (worker)

O worker roda dentro da API (`CADENCE_WORKER_ENABLED=true`):

- O D0 é manual (botão no painel) e agenda o **D2 para 2 dias depois**. O D2 agenda o **D5 para 3 dias depois**; depois do D5 a régua termina.
- Qualquer resposta do barbeiro, ou o descadastro, tira o lead da régua.
- Só envia dentro de `CADENCE_SEND_HOURS` e `CADENCE_SEND_WEEKDAYS` (padrão: 8h–20h, seg–sáb, em Brasília), respeita o limite diário e espera um intervalo aleatório entre `ANTIBAN_MIN_DELAY_SEC` e `ANTIBAN_MAX_DELAY_SEC` entre envios.
- Se o envio falhar, tenta de novo em 30 min.
- Com várias réplicas da API no Postgres, um advisory lock garante que só uma roda o worker.

## Endpoints

Todas as rotas `/api/*` (exceto `/api/auth/login`) exigem sessão.

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/api/leads/scrape` | `{state, city, neighborhood?}`: descobre e salva barbearias (sem duplicar por telefone) |
| `GET` | `/api/leads?state=&city=` | Lista os leads |
| `GET` | `/api/leads/{id}/interactions` | Histórico da conversa |
| `POST` | `/api/leads/{id}/send-d0` | Envia o gancho D0 (`409` se descadastrado, `429` no limite diário) |
| `GET` | `/api/cadence/schedule` | Fila do worker (próximos D2/D5) e envios restantes hoje |
| `POST` | `/api/simulator/inbound` | `{phone, message}`: simula o barbeiro respondendo (só com `WHATSAPP_PROVIDER=MOCK`) |
| `POST` | `/webhook/whatsapp` | Evento `messages.upsert` da Evolution (exige `WEBHOOK_SECRET`) |
| `POST` | `/api/auth/login` · `/api/auth/logout` | Abre e fecha a sessão |
| `GET` | `/api/auth/me` | E-mail da sessão atual |
| `GET` | `/api/metrics` | KPIs do painel |
| `POST` | `/api/settings/trial-mode` | `{trial_mode: "STAND_BY" \| "AUTO_LINK"}` |
| `GET` | `/health` | Healthcheck |

## Usando o painel

1. Entre com o e-mail e a senha do admin.
2. Escolha a UF e a cidade e clique em **Buscar Barbearias**. Leads gerados localmente aparecem com o selo **Fictício**.
3. Em um card da coluna *Detectadas*, clique em **Enviar D0**. O card passa a mostrar quando sai o D2 automático.
4. No **Simulador Webhook WhatsApp** (só em modo `MOCK`), use as respostas rápidas (interesse, as 4 objeções e descadastro) ou digite uma mensagem. Ela passa pelo grafo real da API.
5. No header, alterne o **Modo Trial** e veja o Agente 5 trocar a fila VIP pelo envio do link `/{slug}`.

## Variáveis de ambiente

Veja `.env.example` (API) e `frontend/.env.example` (painel). Principais:

- `WHATSAPP_PROVIDER`: `MOCK` (padrão) ou `EVOLUTION`, junto com `EVOLUTION_BASE_URL`, `EVOLUTION_INSTANCE` e `EVOLUTION_API_KEY`.
- `TRIAL_MODE`: modo inicial do Agente 5.
- `ANTIBAN_DAILY_LIMIT_PER_INSTANCE`: limite diário de envios.
- `FRONTEND_ORIGINS`: origens liberadas no CORS (separadas por vírgula).
- `DATABASE_URL`: aceita `postgresql://` ou `postgresql+asyncpg://`.
- `STORAGE_BACKEND`: `auto` (padrão; Postgres com fallback para memória), `postgres` (a API não sobe sem banco) ou `memory`.
- `DATABASE_CONNECT_ATTEMPTS`: tentativas de conexão na subida, com 2 s entre elas.
- `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `JWT_SECRET`, `SESSION_TTL_HOURS`, `COOKIE_SECURE`: acesso ao painel.
- `WEBHOOK_SECRET`: segredo do webhook da Evolution.
- `GOOGLE_MAPS_API_KEY`: busca real de barbearias (Places API New). Sem ela, os leads são fictícios.
- `CADENCE_WORKER_ENABLED`, `CADENCE_SEND_HOURS`, `CADENCE_SEND_WEEKDAYS`, `CADENCE_POLL_INTERVAL_SEC`, `ANTIBAN_MIN_DELAY_SEC`, `ANTIBAN_MAX_DELAY_SEC`: worker da cadência.

No Docker, coloque os valores reais em `.env`: o compose lê o `.env.example` e depois o `.env`, que sobrescreve.

O modo de trial alterado no painel é salvo na tabela `sdr_settings`; `TRIAL_MODE` vale só até a primeira troca. O limite anti-ban conta os envios desde a meia-noite (horário de Brasília).

## Limitações conhecidas

- No modo `auto`, se o Postgres cair na subida, a API segue em memória e só avisa no log (e em `/health`). Em produção, use `STORAGE_BACKEND=postgres`.
- As métricas carregam todos os leads a cada chamada; acima de alguns milhares, vale trocar por agregações SQL.
- O Google Places não informa o número de cadeiras: leads reais entram como 1 cadeira (Plano Solo) até alguém ajustar. Instagram ainda não é consultado.
- Admin único: não há cadastro de usuários nem papéis.
- As respostas usam templates determinísticos; não há LLM plugado.
- O worker não é aplicado a leads que receberam o D0 antes desta versão (eles não têm `next_touch_at`).
- As respostas do grafo (Agentes 4/5) saem na hora, sem o delay anti-ban, para não travar a conversa.
