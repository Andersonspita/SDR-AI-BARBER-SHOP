# Deploy na VPS (horizonaj.com.br)

O SDR roda na mesma VPS do barber-shop, atrás do proxy com HTTPS do painel da iContainer (OpenResty).

| Item | Valor |
|---|---|
| Domínio | `https://sdr.horizonaj.com.br` (painel e API no mesmo domínio) |
| Pasta na VPS | `/root/sdr-ai` |
| Porta local | `127.0.0.1:8110` (gateway nginx do compose; nada mais fica exposto) |
| Evolution API (perfil `whatsapp`) | `127.0.0.1:8090`, só por túnel SSH |

As portas 3000, 8000 e 8080 da VPS já são usadas por outros sistemas.

## 1. DNS (Cloudflare)

Crie o registro abaixo, igual aos outros subdomínios:

- **Type** `A`
- **Name** `sdr`
- **IPv4** `184.107.179.70`
- **Proxy status** **DNS only** (nuvem cinza): quem emite o certificado é a VPS.

## 2. Site no painel da iContainer

Crie um site de **proxy reverso**:

- **Domínio** `sdr.horizonaj.com.br`
- **Destino** `http://127.0.0.1:8110`
- **HTTPS** com Let's Encrypt (depois que o DNS propagar), forçando HTTPS

É o mesmo esquema do `barber-shop.horizonaj.com.br`.

## 3. Subir / atualizar

```bash
git clone https://github.com/Andersonspita/SDR-AI-BARBER-SHOP.git /root/sdr-ai
```

```bash
cd /root/sdr-ai && cp .env.production.example .env && nano .env
```

Preencha o `.env`. Gere cada segredo com:

```bash
openssl rand -base64 48 | tr -d '/+=' | cut -c1-48
```

Depois suba:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Para atualizar depois:

```bash
cd /root/sdr-ai && git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Confira em `https://sdr.horizonaj.com.br/health` (`"storage": "postgres"`).

## 4. Integração com o barber-shop

No `.env` do SDR:

- `BARBERSHOP_API_URL` e `SAAS_BASE_URL` = `https://barber-shop.horizonaj.com.br`
- `BARBERSHOP_PROVISIONING_KEY` = `PROVISIONING_API_KEY` do barber-shop
- `BARBERSHOP_WEBHOOK_SECRET` = `SDR_WEBHOOK_SECRET` do barber-shop

No `.env` do barber-shop (`/root/barber-shop`):

- `SDR_WEBHOOK_URL=https://sdr.horizonaj.com.br/webhook/barbershop`
- `APP_URL=https://barber-shop.horizonaj.com.br`. Os links de senha e de assinatura usam esse valor.

**Ordem:** o SDR vai ao ar antes do barber-shop que manda os avisos pela conversa do SDR.

## 5. WhatsApp (quando for ligar)

1. Suba com o perfil: `docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile whatsapp up -d`.
2. Abra um túnel e conecte o número pelo QR code em `http://localhost:8090`:

   ```bash
   ssh -L 8090:127.0.0.1:8090 root@184.107.179.70
   ```

3. Troque `WHATSAPP_PROVIDER=EVOLUTION` no `.env` e suba de novo. Com WhatsApp real, a API recusa subir se os segredos estiverem fracos.
