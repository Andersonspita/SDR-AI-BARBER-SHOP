from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set
import asyncpg
from backend.app.models import BarbershopLead, Interaction
from backend.app.repositories.base import MIN_PHONE_DIGITS, PHONE_SUFFIX_LENGTH, phone_digits


logger = logging.getLogger(__name__)

# Chave arbitrária (fixa) do advisory lock que elege o worker de cadência entre réplicas.
CADENCE_WORKER_LOCK_KEY = 7_400_210_226

# database/init.sql na raiz do projeto (no container: /srv/database/init.sql)
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parents[3] / "database" / "init.sql"

_LEAD_COLUMNS = (
    "id, shop_name, slug, owner_name, phone, instagram_handle, bio_url, competitor_name, "
    "estimated_barbers, current_system, priority, status, city, state, neighborhood, "
    "langgraph_node, last_objection_type, source, cadence_step, next_touch_at, "
    "email, trial_shop_id, trial_status, trial_ends_at, subscription_status, plan_code, "
    "invoice_url, subscribe_url, suspends_at, needs_billing_help, created_at"
)
_LEAD_SELECT = f"SELECT {_LEAD_COLUMNS} FROM barbershop_leads"
_DIGITS_SQL = "regexp_replace(phone, '\\D', '', 'g')"


def normalize_dsn(database_url: str) -> str:
    """Aceita o formato SQLAlchemy (`postgresql+asyncpg://`) usado no .env e devolve o DSN do asyncpg."""
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def _row_to_lead(row: asyncpg.Record) -> BarbershopLead:
    return BarbershopLead(
        id=str(row["id"]),
        shop_name=row["shop_name"],
        slug=row["slug"],
        owner_name=row["owner_name"],
        phone=row["phone"],
        instagram_handle=row["instagram_handle"],
        bio_url=row["bio_url"],
        competitor_name=row["competitor_name"],
        estimated_barbers=row["estimated_barbers"] or 1,
        current_system=row["current_system"],
        priority=row["priority"],
        status=row["status"],
        city=row["city"],
        state=row["state"],
        neighborhood=row["neighborhood"],
        langgraph_node=row["langgraph_node"] or "1.Scraper",
        last_objection_type=row["last_objection_type"],
        source=row["source"],
        cadence_step=row["cadence_step"],
        next_touch_at=row["next_touch_at"].isoformat() if row["next_touch_at"] else None,
        email=row["email"],
        trial_shop_id=row["trial_shop_id"],
        trial_status=row["trial_status"],
        trial_ends_at=row["trial_ends_at"].isoformat() if row["trial_ends_at"] else None,
        subscription_status=row["subscription_status"],
        plan_code=row["plan_code"],
        invoice_url=row["invoice_url"],
        subscribe_url=row["subscribe_url"],
        suspends_at=row["suspends_at"].isoformat() if row["suspends_at"] else None,
        needs_billing_help=bool(row["needs_billing_help"]),
        created_at=row["created_at"].isoformat(),
    )


def _row_to_interaction(row: asyncpg.Record) -> Interaction:
    return Interaction(
        id=str(row["id"]),
        lead_id=str(row["lead_id"]),
        direction=row["direction"],
        message_text=row["message_text"],
        channel=row["channel"],
        intent_detected=row["intent_detected"],
        langgraph_node=row["langgraph_node"],
        sent_at=row["sent_at"].isoformat(),
    )


class PostgresRepository:
    """Persistência em PostgreSQL via pool asyncpg, usando o schema de `database/init.sql`."""

    backend_name = "postgres"

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._lock_conn: Optional[asyncpg.Connection] = None

    @classmethod
    async def connect(
        cls,
        database_url: str,
        schema_path: Path = DEFAULT_SCHEMA_PATH,
        attempts: int = 3,
        retry_delay_sec: float = 2.0,
        timeout_sec: float = 5.0,
    ) -> "PostgresRepository":
        """Abre o pool (com retentativas, para quando o Postgres ainda está subindo) e aplica o schema."""
        last_error: Optional[BaseException] = None
        for attempt in range(1, attempts + 1):
            try:
                pool = await asyncpg.create_pool(
                    normalize_dsn(database_url), min_size=1, max_size=10, timeout=timeout_sec
                )
                break
            except (OSError, asyncpg.PostgresError, asyncio.TimeoutError) as exc:
                last_error = exc
                logger.warning("Postgres indisponível (tentativa %d/%d): %s", attempt, attempts, exc)
                if attempt < attempts:
                    await asyncio.sleep(retry_delay_sec)
        else:
            raise ConnectionError(f"Não foi possível conectar ao Postgres: {last_error}") from last_error

        repo = cls(pool)
        try:
            await repo.ensure_schema(schema_path)
        except Exception:
            await pool.close()
            raise
        return repo

    async def ensure_schema(self, schema_path: Path = DEFAULT_SCHEMA_PATH) -> None:
        sql = schema_path.read_text(encoding="utf-8")
        async with self._pool.acquire() as conn:
            await conn.execute(sql)

    async def upsert_lead(self, lead: BarbershopLead) -> BarbershopLead:
        row = await self._pool.fetchrow(
            f"""
            INSERT INTO barbershop_leads ({_LEAD_COLUMNS})
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28, $29, $30, $31)
            ON CONFLICT (phone) DO NOTHING
            RETURNING {_LEAD_COLUMNS}
            """,
            lead.id, lead.shop_name, lead.slug, lead.owner_name, lead.phone, lead.instagram_handle,
            lead.bio_url, lead.competitor_name, lead.estimated_barbers, lead.current_system.value,
            lead.priority, lead.status.value, lead.city, lead.state, lead.neighborhood,
            lead.langgraph_node, lead.last_objection_type, lead.source.value, lead.cadence_step,
            _parse_ts(lead.next_touch_at), lead.email, lead.trial_shop_id,
            lead.trial_status.value if lead.trial_status else None, _parse_ts(lead.trial_ends_at),
            lead.subscription_status, lead.plan_code, lead.invoice_url, lead.subscribe_url,
            _parse_ts(lead.suspends_at), lead.needs_billing_help,
            datetime.fromisoformat(lead.created_at),
        )
        if row is None:
            row = await self._pool.fetchrow(f"{_LEAD_SELECT} WHERE phone = $1", lead.phone)
        return _row_to_lead(row)

    async def save_lead(self, lead: BarbershopLead) -> None:
        await self._pool.execute(
            """
            UPDATE barbershop_leads SET
                shop_name = $2, owner_name = $3, instagram_handle = $4, bio_url = $5, competitor_name = $6,
                estimated_barbers = $7, current_system = $8, priority = $9, status = $10, neighborhood = $11,
                langgraph_node = $12, last_objection_type = $13, cadence_step = $14, next_touch_at = $15,
                email = $16, trial_shop_id = $17, trial_status = $18, trial_ends_at = $19, slug = $20,
                subscription_status = $21, plan_code = $22, invoice_url = $23, subscribe_url = $24,
                suspends_at = $25, needs_billing_help = $26,
                updated_at = NOW()
            WHERE id = $1
            """,
            lead.id, lead.shop_name, lead.owner_name, lead.instagram_handle, lead.bio_url, lead.competitor_name,
            lead.estimated_barbers, lead.current_system.value, lead.priority, lead.status.value,
            lead.neighborhood, lead.langgraph_node, lead.last_objection_type, lead.cadence_step,
            _parse_ts(lead.next_touch_at), lead.email, lead.trial_shop_id,
            lead.trial_status.value if lead.trial_status else None, _parse_ts(lead.trial_ends_at), lead.slug,
            lead.subscription_status, lead.plan_code, lead.invoice_url, lead.subscribe_url,
            _parse_ts(lead.suspends_at), lead.needs_billing_help,
        )

    async def get_lead(self, lead_id: str) -> Optional[BarbershopLead]:
        if not _is_uuid(lead_id):
            return None
        row = await self._pool.fetchrow(f"{_LEAD_SELECT} WHERE id = $1", lead_id)
        return _row_to_lead(row) if row else None

    async def get_lead_by_phone(self, phone: str) -> Optional[BarbershopLead]:
        clean = phone_digits(phone)
        if len(clean) < MIN_PHONE_DIGITS:
            return None
        row = await self._pool.fetchrow(
            f"""
            {_LEAD_SELECT}
            WHERE {_DIGITS_SQL} = $1 OR {_DIGITS_SQL} LIKE '%' || $2
            ORDER BY ({_DIGITS_SQL} = $1) DESC, created_at
            LIMIT 1
            """,
            clean,
            clean[-PHONE_SUFFIX_LENGTH:],
        )
        return _row_to_lead(row) if row else None

    async def list_leads(self, state: Optional[str] = None, city: Optional[str] = None) -> List[BarbershopLead]:
        rows = await self._pool.fetch(
            f"""
            {_LEAD_SELECT}
            WHERE ($1::text IS NULL OR upper(state) = upper($1))
              AND ($2::text IS NULL OR lower(city) = lower($2))
            ORDER BY created_at, id
            """,
            state or None,
            city or None,
        )
        return [_row_to_lead(row) for row in rows]

    async def list_due_leads(self, now: datetime, limit: int = 10) -> List[BarbershopLead]:
        rows = await self._pool.fetch(
            f"{_LEAD_SELECT} WHERE next_touch_at IS NOT NULL AND next_touch_at <= $1 ORDER BY next_touch_at LIMIT $2",
            now,
            limit,
        )
        return [_row_to_lead(row) for row in rows]

    async def add_interaction(self, interaction: Interaction) -> Interaction:
        await self._pool.execute(
            """
            INSERT INTO interactions (id, lead_id, channel, direction, message_text, intent_detected, langgraph_node, sent_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            """,
            interaction.id, interaction.lead_id, interaction.channel, interaction.direction,
            interaction.message_text, interaction.intent_detected, interaction.langgraph_node,
            datetime.fromisoformat(interaction.sent_at),
        )
        return interaction

    async def get_lead_interactions(self, lead_id: str) -> List[Interaction]:
        if not _is_uuid(lead_id):
            return []
        rows = await self._pool.fetch(
            "SELECT * FROM interactions WHERE lead_id = $1 ORDER BY sent_at, seq", lead_id
        )
        return [_row_to_interaction(row) for row in rows]

    async def count_outbound_since(self, since: datetime) -> int:
        return await self._pool.fetchval(
            "SELECT count(*) FROM interactions WHERE direction = 'OUTBOUND' AND sent_at >= $1", since
        )

    async def replied_lead_ids(self) -> Set[str]:
        rows = await self._pool.fetch("SELECT DISTINCT lead_id FROM interactions WHERE direction = 'INBOUND'")
        return {str(row["lead_id"]) for row in rows}

    async def get_setting(self, key: str) -> Optional[str]:
        return await self._pool.fetchval("SELECT value FROM sdr_settings WHERE key = $1", key)

    async def set_setting(self, key: str, value: str) -> None:
        await self._pool.execute(
            """
            INSERT INTO sdr_settings (key, value) VALUES ($1, $2)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
            """,
            key,
            value,
        )

    async def mark_event_processed(self, event_id: str) -> bool:
        inserted = await self._pool.fetchval(
            "INSERT INTO processed_webhook_events (id) VALUES ($1) ON CONFLICT (id) DO NOTHING RETURNING id",
            event_id,
        )
        return inserted is not None

    async def try_acquire_worker_lock(self) -> bool:
        """Advisory lock de sessão: fica com quem pegou até a conexão fechar (ou o processo morrer)."""
        if self._lock_conn is not None:
            return True
        conn = await self._pool.acquire()
        try:
            acquired = await conn.fetchval("SELECT pg_try_advisory_lock($1)", CADENCE_WORKER_LOCK_KEY)
        except Exception:
            await self._pool.release(conn)
            raise
        if not acquired:
            await self._pool.release(conn)
            return False
        self._lock_conn = conn
        return True

    async def close(self) -> None:
        if self._lock_conn is not None:
            try:
                await self._lock_conn.execute("SELECT pg_advisory_unlock($1)", CADENCE_WORKER_LOCK_KEY)
            finally:
                await self._pool.release(self._lock_conn)
                self._lock_conn = None
        await self._pool.close()
