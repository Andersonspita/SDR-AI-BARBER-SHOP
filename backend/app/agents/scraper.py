from __future__ import annotations
import hashlib
import logging
import os
from typing import List, Optional
import httpx
from backend.app.models import BarbershopLead, LeadSource, LeadStatus, TechMaturity


logger = logging.getLogger(__name__)


COMPETITOR_SIGNATURES = {
    "appbarber": "AppBarber",
    "booksy": "Booksy",
    "trinks": "Trinks",
    "gendo": "Gendo",
    "avec": "Avec",
    "cashbarber": "CashBarber",
    "bber": "Bber",
}

WHATSAPP_SIGNATURES = (
    "wa.me/",
    "api.whatsapp.com",
    "whats.link",
    "wa.link",
    "whatsapp.com/channel",
)


def detect_bio_system(bio_url: Optional[str], bio_text: str = "") -> dict:
    """
    Agente 1: Detecta a maturidade tecnológica da barbearia pela URL da bio do Instagram.
    - Se contém wa.me / api.whatsapp.com -> MANUAL_WHATSAPP (Prioridade ALTA)
    - Se contém concorrente (AppBarber, Booksy, Trinks, Gendo) -> COMPETITOR (Prioridade MIGRACAO)
    - Se não tem link -> UNKNOWN (Prioridade MEDIA)
    """
    combined = f"{bio_url or ''} {bio_text or ''}".lower().strip()
    if not combined:
        return {
            "current_system": TechMaturity.UNKNOWN,
            "competitor_name": None,
            "priority": "MEDIA",
        }

    for pattern, comp_name in COMPETITOR_SIGNATURES.items():
        if pattern in combined:
            return {
                "current_system": TechMaturity.COMPETITOR,
                "competitor_name": comp_name,
                "priority": "MIGRACAO",
            }

    for wa_pattern in WHATSAPP_SIGNATURES:
        if wa_pattern in combined:
            return {
                "current_system": TechMaturity.MANUAL_WHATSAPP,
                "competitor_name": None,
                "priority": "ALTA",
            }

    return {
        "current_system": TechMaturity.UNKNOWN,
        "competitor_name": None,
        "priority": "MEDIA",
    }


DDD_BY_STATE = {
    "BA": "77",
    "SP": "11",
    "RJ": "21",
    "MG": "31",
    "PR": "41",
    "SC": "48",
    "RS": "51",
    "PE": "81",
    "CE": "85",
    "GO": "62",
    "DF": "61",
}


def scrape_barbershops_by_location(
    state: str, city: str, neighborhood: Optional[str] = None
) -> List[BarbershopLead]:
    """
    Agente 1: Busca barbearias por Estado (UF) e Cidade (Google Maps / Instagram Bio Scraper)
    e retorna os leads enriquecidos com maturidade tecnológica, cadeiras estimadas e slug.
    """
    uf = (state or "BA").upper().strip()[:2]
    city_clean = (city or "Vitória da Conquista").strip()
    bairro = (neighborhood or "Centro").strip()
    ddd = DDD_BY_STATE.get(uf, "11")

    # Templates realistas de barbearias locais enriquecidas
    templates = [
        {
            "suffix": "Barbearia Elite",
            "owner": "Marcos Oliveira",
            "chairs": 1,
            "bio_url_fmt": "https://wa.me/55{phone}?text=Quero+agendar+corte",
        },
        {
            "suffix": "Studio Corte Fino",
            "owner": "Rafael Santos",
            "chairs": 3,
            "bio_url_fmt": "https://wa.me/55{phone}",
        },
        {
            "suffix": "Viking Barber Club",
            "owner": "Lucas Almeida",
            "chairs": 6,
            "bio_url_fmt": "https://sites.appbarber.com.br/viking-{slug_city}",
        },
        {
            "suffix": "Barbearia Dom Bigode",
            "owner": "Diego Costa",
            "chairs": 1,
            "bio_url_fmt": "https://api.whatsapp.com/send?phone=55{phone}",
        },
        {
            "suffix": "Navalha de Ouro",
            "owner": "Bruno Ferreira",
            "chairs": 4,
            "bio_url_fmt": "https://booksy.com/pt-br/navalha-{slug_city}",
        },
        {
            "suffix": "Barbearia Imperial",
            "owner": "Thiago Mendes",
            "chairs": 8,
            "bio_url_fmt": None,
        },
    ]

    slug_city = city_clean.lower().replace(" ", "-")
    results: List[BarbershopLead] = []

    for idx, item in enumerate(templates):
        shop_name = (
            item["suffix"]
            if city_clean.lower() == "vitória da conquista" or idx == 0
            else f"{item['suffix']} {bairro if neighborhood else city_clean.split()[0]}"
        )
        seed = hashlib.md5(f"{uf}:{city_clean}:{shop_name}".encode()).hexdigest()
        num_tail = str(int(seed[:7], 16) % 9000000 + 1000000)
        phone = f"55{ddd}99{num_tail}"

        bio_url = (
            item["bio_url_fmt"].format(phone=f"{ddd}99{num_tail}", slug_city=slug_city)
            if item["bio_url_fmt"]
            else None
        )
        detection = detect_bio_system(bio_url)
        ig_handle = "@" + shop_name.lower().replace(" ", "").replace("ã", "a").replace("ó", "o")[:20]

        lead = BarbershopLead(
            shop_name=shop_name,
            owner_name=item["owner"],
            phone=phone,
            instagram_handle=ig_handle,
            bio_url=bio_url,
            competitor_name=detection["competitor_name"],
            estimated_barbers=item["chairs"],
            current_system=detection["current_system"],
            priority=detection["priority"],
            status=LeadStatus.ENRICHED,
            city=city_clean,
            state=uf,
            neighborhood=bairro,
            langgraph_node="1.Scraper",
            source=LeadSource.SYNTHETIC,
        )
        results.append(lead)

    return results


GOOGLE_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
GOOGLE_PLACES_FIELD_MASK = ",".join(
    (
        "places.id",
        "places.displayName",
        "places.internationalPhoneNumber",
        "places.websiteUri",
        "places.formattedAddress",
    )
)


class ScraperError(Exception):
    """Falha ao consultar uma fonte real de barbearias (Google Places, Apify)."""


def br_phone_digits(raw_phone: Optional[str]) -> Optional[str]:
    """
    Telefone brasileiro só com dígitos e DDI (55 + DDD + 8 ou 9 dígitos), ou None se não for um.
    Aceita o número com ou sem o 55: "(77) 99911-2233" e "+55 77 99911-2233" viram "5577999112233".
    """
    digits = "".join(ch for ch in (raw_phone or "") if ch.isdigit())
    if (raw_phone or "").strip().startswith("+") and not digits.startswith("55"):
        return None  # número estrangeiro
    if digits.startswith("0"):
        digits = digits.lstrip("0")  # 0 + DDD de discagem interurbana
    if len(digits) in (10, 11):
        digits = "55" + digits  # sem DDI: DDD + número
    if not (digits.startswith("55") and len(digits) in (12, 13)):
        return None
    ddd = digits[2:4]
    return digits if ddd[0] != "0" and ddd[1] != "0" else None


async def search_google_places(
    state: str,
    city: str,
    neighborhood: Optional[str],
    api_key: str,
    http_client: Optional[httpx.AsyncClient] = None,
    page_size: int = 20,
) -> List[BarbershopLead]:
    """
    Agente 1 (fonte real): Text Search da Places API (New). Barbearias sem telefone brasileiro são
    descartadas. O Google não informa o número de cadeiras, então `estimated_barbers` fica 1 (Plano Solo)
    até alguém ajustar o lead.
    """
    uf = (state or "").upper().strip()[:2]
    place = f"{neighborhood.strip()}, {city.strip()}" if neighborhood and neighborhood.strip() else city.strip()
    payload = {"textQuery": f"barbearia em {place} - {uf}", "languageCode": "pt-BR", "regionCode": "BR", "pageSize": page_size}
    headers = {"X-Goog-Api-Key": api_key, "X-Goog-FieldMask": GOOGLE_PLACES_FIELD_MASK}

    client = http_client or httpx.AsyncClient(timeout=15.0)
    try:
        response = await client.post(GOOGLE_PLACES_SEARCH_URL, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise ScraperError(f"Google Places indisponível: {exc}") from exc
    finally:
        if http_client is None:
            await client.aclose()

    if response.status_code != 200:
        try:
            message = response.json().get("error", {}).get("message", "")
        except ValueError:
            message = response.text[:200]
        raise ScraperError(f"Google Places recusou a busca ({response.status_code}): {message}")

    leads: List[BarbershopLead] = []
    for place_data in response.json().get("places", []):
        phone = br_phone_digits(place_data.get("internationalPhoneNumber"))
        name = (place_data.get("displayName") or {}).get("text")
        if not phone or not name:
            continue
        website = place_data.get("websiteUri")
        detection = detect_bio_system(website)
        leads.append(
            BarbershopLead(
                shop_name=name,
                phone=phone,
                city=city.strip(),
                state=uf,
                neighborhood=neighborhood.strip() if neighborhood else None,
                bio_url=website,
                competitor_name=detection["competitor_name"],
                current_system=detection["current_system"],
                priority=detection["priority"],
                status=LeadStatus.ENRICHED,
                source=LeadSource.GOOGLE_PLACES,
            )
        )
    logger.info("Google Places: %d barbearias com telefone em %s/%s", len(leads), city, uf)
    return leads


async def discover_barbershops(
    state: str,
    city: str,
    neighborhood: Optional[str] = None,
    http_client: Optional[httpx.AsyncClient] = None,
) -> List[BarbershopLead]:
    """
    Ponto de entrada síncrono do Agente 1: com GOOGLE_MAPS_API_KEY busca barbearias reais; sem a chave
    usa o gerador local, cujos leads saem marcados como SYNTHETIC (bloqueados para WhatsApp real).
    Com APIFY_TOKEN (e sem a chave do Google) a busca é assíncrona: ver `agents/apify_maps.py`.
    """
    api_key = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
    if not api_key:
        return scrape_barbershops_by_location(state, city, neighborhood)
    return await search_google_places(state, city, neighborhood, api_key, http_client=http_client)


def active_lead_source() -> str:
    """Fonte usada pelo botão de busca: GOOGLE_PLACES, APIFY ou SYNTHETIC (nessa ordem de preferência)."""
    if os.getenv("GOOGLE_MAPS_API_KEY", "").strip():
        return "GOOGLE_PLACES"
    if os.getenv("APIFY_TOKEN", "").strip():
        return "APIFY"
    return "SYNTHETIC"
