"""
Agente 1 (fonte alternativa): barbearias do Google Maps pela ferramenta "Google Maps Scraper" do Apify.

Serve para quem não tem chave da Places API: o Apify só pede um token (conta grátis, sem cartão, com
US$ 5 de crédito por mês). Uma busca leva de 1 a 3 minutos, mais do que os proxies na frente do SDR
seguram uma requisição, então ela é assíncrona: `start_search` dispara a execução e devolve o id;
`fetch_search` diz se terminou e, quando termina, devolve as barbearias.

Token: `APIFY_TOKEN`. Limite de lugares por busca: `APIFY_MAX_PLACES` (padrão 20), que também é o
teto de cobrança da execução.
"""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional
import httpx
from backend.app.agents.scraper import ScraperError, br_phone_digits, detect_bio_system
from backend.app.models import BarbershopLead, LeadSource, LeadStatus


logger = logging.getLogger(__name__)

APIFY_API_URL = "https://api.apify.com/v2"
GOOGLE_MAPS_ACTOR = "compass~crawler-google-places"
DEFAULT_MAX_PLACES = 20
RUN_TIMEOUT_SEC = 300
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9]{8,32}$")

PENDING_STATUSES = {"READY", "RUNNING"}
FAILED_STATUSES = {"FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT", "ABORTING"}


def apify_token() -> str:
    return os.getenv("APIFY_TOKEN", "").strip()


def max_places() -> int:
    try:
        value = int(os.getenv("APIFY_MAX_PLACES", DEFAULT_MAX_PLACES))
    except ValueError:
        return DEFAULT_MAX_PLACES
    return max(1, min(value, 200))


def is_valid_run_id(run_id: str) -> bool:
    return bool(RUN_ID_PATTERN.match(run_id or ""))


@dataclass
class ApifySearch:
    """Situação de uma busca: `running`, `done` (com `leads`) ou erro (levanta ScraperError)."""

    status: str
    leads: List[BarbershopLead] = field(default_factory=list)


def _headers(token: str) -> dict:
    # Token no cabeçalho, não na URL: assim ele não aparece em logs de proxy.
    return {"Authorization": f"Bearer {token}"}


def build_actor_input(state: str, city: str, neighborhood: Optional[str], limit: int) -> dict:
    uf = (state or "").upper().strip()[:2]
    place = f"{neighborhood.strip()}, {city.strip()}" if neighborhood and neighborhood.strip() else city.strip()
    return {
        "searchStringsArray": ["barbearia"],
        "locationQuery": f"{place}, {uf}, Brasil",
        "maxCrawledPlacesPerSearch": limit,
        "language": "pt-BR",
        "skipClosedPlaces": True,
        # Só os dados da lista de resultados (nome, telefone, site): sem página de detalhe, avaliações
        # nem enriquecimento de contatos, que são cobrados à parte.
        "scrapePlaceDetailPage": False,
        "scrapeContacts": False,
        "maxReviews": 0,
        "maxImages": 0,
    }


async def _request(method: str, path: str, token: str, client: Optional[httpx.AsyncClient], **kwargs) -> dict | list:
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await http.request(method, f"{APIFY_API_URL}{path}", headers=_headers(token), **kwargs)
    except httpx.HTTPError as exc:
        raise ScraperError(f"Apify indisponível: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()
    if response.status_code == 401:
        raise ScraperError("Apify recusou o token (APIFY_TOKEN inválido ou revogado).")
    if response.status_code == 402:
        raise ScraperError("Crédito do Apify esgotado neste mês. Aguarde a renovação ou adicione crédito na conta.")
    if response.status_code >= 400:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = response.text[:200]
        raise ScraperError(f"Apify recusou a busca ({response.status_code}): {message}")
    return response.json()


async def start_search(
    state: str,
    city: str,
    neighborhood: Optional[str] = None,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> str:
    """Dispara a busca no Apify e devolve o id da execução."""
    token = token or apify_token()
    limit = max_places()
    body = await _request(
        "POST",
        f"/acts/{GOOGLE_MAPS_ACTOR}/runs",
        token,
        client,
        params={"timeout": RUN_TIMEOUT_SEC, "maxItems": limit},
        json=build_actor_input(state, city, neighborhood, limit),
    )
    run_id = (body.get("data") or {}).get("id") if isinstance(body, dict) else None
    if not run_id:
        raise ScraperError("Apify não devolveu o id da busca.")
    logger.info("Apify: busca %s iniciada em %s/%s", run_id, city, state)
    return run_id


async def fetch_search(
    run_id: str,
    state: str,
    city: str,
    neighborhood: Optional[str] = None,
    token: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> ApifySearch:
    """Consulta a execução; quando terminou, lê os resultados e converte em leads."""
    token = token or apify_token()
    run = await _request("GET", f"/actor-runs/{run_id}", token, client)
    data = (run.get("data") if isinstance(run, dict) else None) or {}
    status = data.get("status", "")
    if status in PENDING_STATUSES:
        return ApifySearch(status="running")
    if status in FAILED_STATUSES or status != "SUCCEEDED":
        raise ScraperError(f"A busca no Apify não terminou bem (situação: {status or 'desconhecida'}).")

    items = await _request(
        "GET",
        f"/datasets/{data.get('defaultDatasetId')}/items",
        token,
        client,
        params={"clean": "true", "format": "json"},
    )
    leads = leads_from_items(items if isinstance(items, list) else [], state, city, neighborhood)
    logger.info("Apify: %d barbearias com telefone em %s/%s", len(leads), city, state)
    return ApifySearch(status="done", leads=leads)


def _looks_like_barbershop(item: dict) -> bool:
    """A busca por "barbearia" também traz salões de beleza: fica quem é barbearia pelo nome ou categoria."""
    labels = [item.get("title") or "", item.get("categoryName") or "", *(item.get("categories") or [])]
    return any("barb" in str(label).lower() for label in labels)


def leads_from_items(items: list, state: str, city: str, neighborhood: Optional[str]) -> List[BarbershopLead]:
    uf = (state or "").upper().strip()[:2]
    leads: List[BarbershopLead] = []
    for item in items:
        if not isinstance(item, dict) or item.get("permanentlyClosed") or item.get("temporarilyClosed"):
            continue
        name = (item.get("title") or "").strip()
        phone = br_phone_digits(item.get("phoneUnformatted") or item.get("phone"))
        if not name or not phone or not _looks_like_barbershop(item):
            continue
        website = item.get("website")
        detection = detect_bio_system(website)
        leads.append(
            BarbershopLead(
                shop_name=name,
                phone=phone,
                city=city.strip(),
                state=uf,
                neighborhood=(neighborhood or "").strip() or item.get("neighborhood") or None,
                bio_url=website,
                competitor_name=detection["competitor_name"],
                current_system=detection["current_system"],
                priority=detection["priority"],
                status=LeadStatus.ENRICHED,
                source=LeadSource.APIFY_GOOGLE_MAPS,
            )
        )
    return leads
