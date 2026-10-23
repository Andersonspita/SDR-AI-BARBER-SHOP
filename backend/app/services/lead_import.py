"""
Importação de barbearias por planilha (CSV) no painel.

Serve para qualquer origem que não seja a busca automática: anotações do Google Maps ou do
Instagram, indicações, listas. Aceita `;` ou `,` como separador (o Excel em português salva com `;`),
cabeçalhos em português com ou sem acento e telefone em qualquer formato brasileiro.

Colunas: nome e telefone são obrigatórios; cidade e UF também, mas podem vir do formulário para a
planilha inteira. Instagram, site, dono, bairro e profissionais são opcionais.
"""
from __future__ import annotations
import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from backend.app.agents.scraper import br_phone_digits, detect_bio_system
from backend.app.models import BarbershopLead, LeadSource, LeadStatus

MAX_CSV_BYTES = 1_000_000
MAX_ROWS = 2_000

UF_BY_NAME = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso": "MT",
    "mato grosso do sul": "MS", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR",
    "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP",
    "sergipe": "SE", "tocantins": "TO",
}
VALID_UFS = set(UF_BY_NAME.values())

# Nome do campo do lead -> cabeçalhos aceitos (já sem acento e em minúsculas).
COLUMN_ALIASES: Dict[str, tuple] = {
    "shop_name": ("nome", "barbearia", "nome da barbearia", "estabelecimento", "empresa", "shop_name", "name"),
    "phone": ("telefone", "whatsapp", "whats", "celular", "fone", "contato", "phone", "numero"),
    "city": ("cidade", "municipio", "city"),
    "state": ("uf", "estado", "state"),
    "neighborhood": ("bairro", "neighborhood"),
    "instagram": ("instagram", "insta", "ig", "instagram_handle"),
    "website": ("site", "link", "link da bio", "bio", "website", "url"),
    "owner_name": ("dono", "responsavel", "proprietario", "contato nome", "owner", "owner_name"),
    "estimated_barbers": ("profissionais", "barbeiros", "cadeiras", "funcionarios"),
}

TEMPLATE_CSV = (
    "nome;telefone;cidade;uf;bairro;instagram;site;dono;profissionais\n"
    "Barbearia Exemplo;(77) 99911-2233;Vitória da Conquista;BA;Centro;@barbeariaexemplo;;João;2\n"
)


def _fold(text: str) -> str:
    """Minúsculas, sem acento e sem espaços sobrando: "Município " -> "municipio"."""
    normalized = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(ch for ch in normalized if not unicodedata.combining(ch)).lower().split())


def normalize_uf(value: Optional[str]) -> Optional[str]:
    folded = _fold(value or "")
    if not folded:
        return None
    if len(folded) == 2 and folded.upper() in VALID_UFS:
        return folded.upper()
    return UF_BY_NAME.get(folded)


def _instagram_handle(value: str) -> Optional[str]:
    """Aceita "@perfil", "perfil" ou o link do perfil."""
    match = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", value or "")
    handle = match.group(1) if match else (value or "").strip().lstrip("@")
    return f"@{handle}" if re.fullmatch(r"[A-Za-z0-9_.]{1,30}", handle) else None


@dataclass
class ImportProblem:
    line: int
    reason: str


@dataclass
class ParsedImport:
    leads: List[BarbershopLead] = field(default_factory=list)
    problems: List[ImportProblem] = field(default_factory=list)
    duplicates_in_file: int = 0


class ImportFormatError(ValueError):
    """A planilha não pode ser lida (vazia, grande demais, sem as colunas obrigatórias)."""


def _map_headers(headers: List[str]) -> Dict[str, int]:
    folded = [_fold(h).lstrip("﻿") for h in headers]
    mapping: Dict[str, int] = {}
    for field_name, aliases in COLUMN_ALIASES.items():
        for index, header in enumerate(folded):
            if header in aliases:
                mapping[field_name] = index
                break
    return mapping


def parse_leads_csv(
    content: str,
    default_state: Optional[str] = None,
    default_city: Optional[str] = None,
) -> ParsedImport:
    """Lê a planilha e devolve os leads válidos e o motivo de cada linha recusada."""
    if not content or not content.strip():
        raise ImportFormatError("A planilha está vazia.")
    if len(content.encode("utf-8")) > MAX_CSV_BYTES:
        raise ImportFormatError("A planilha passa de 1 MB. Divida em arquivos menores.")

    first_line = content.lstrip("﻿").splitlines()[0]
    delimiter = ";" if first_line.count(";") >= first_line.count(",") else ","
    rows = list(csv.reader(io.StringIO(content.lstrip("﻿")), delimiter=delimiter))
    columns = _map_headers(rows[0])
    missing = [label for key, label in (("shop_name", "nome"), ("phone", "telefone")) if key not in columns]
    if missing:
        raise ImportFormatError(
            f"Faltam as colunas: {', '.join(missing)}. Use a primeira linha como cabeçalho (baixe o modelo)."
        )
    if len(rows) - 1 > MAX_ROWS:
        raise ImportFormatError(f"A planilha tem mais de {MAX_ROWS} linhas. Divida em arquivos menores.")

    fallback_uf = normalize_uf(default_state)
    fallback_city = (default_city or "").strip()
    result = ParsedImport()
    seen_phones = set()

    for line_number, row in enumerate(rows[1:], start=2):
        if not any(cell.strip() for cell in row):
            continue

        def cell(name: str) -> str:
            index = columns.get(name)
            return row[index].strip() if index is not None and index < len(row) else ""

        name = cell("shop_name")
        phone = br_phone_digits(cell("phone"))
        uf = normalize_uf(cell("state")) or fallback_uf
        city = cell("city") or fallback_city
        if not name:
            result.problems.append(ImportProblem(line_number, "sem nome"))
            continue
        if not phone:
            result.problems.append(ImportProblem(line_number, f"telefone inválido: “{cell('phone') or 'vazio'}”"))
            continue
        if not uf:
            result.problems.append(ImportProblem(line_number, "sem UF (preencha a coluna ou o campo UF do formulário)"))
            continue
        if not city:
            result.problems.append(ImportProblem(line_number, "sem cidade (preencha a coluna ou o campo do formulário)"))
            continue
        if phone in seen_phones:
            result.duplicates_in_file += 1
            continue
        seen_phones.add(phone)

        instagram = _instagram_handle(cell("instagram")) if cell("instagram") else None
        website = cell("website") or None
        detection = detect_bio_system(website)
        try:
            barbers = max(1, min(int(cell("estimated_barbers")), 50)) if cell("estimated_barbers") else 1
        except ValueError:
            barbers = 1
        result.leads.append(
            BarbershopLead(
                shop_name=name[:120],
                phone=phone,
                city=city[:80],
                state=uf,
                neighborhood=cell("neighborhood")[:80] or None,
                owner_name=cell("owner_name")[:80] or None,
                instagram_handle=instagram,
                bio_url=website,
                competitor_name=detection["competitor_name"],
                current_system=detection["current_system"],
                priority=detection["priority"],
                estimated_barbers=barbers,
                status=LeadStatus.ENRICHED,
                source=LeadSource.CSV_IMPORT,
            )
        )
    return result
