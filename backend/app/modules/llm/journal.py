"""Журнал вызовов LLM: строки `llm_call`, кэш ответов по хэшу, дневной счётчик токенов.

- `compute_prompt_hash(kind, prompt_version, model, variables)` = sha256(kind + версия +
  модель + канонический JSON входа) — ключ кэша и колонка `llm_call.prompt_hash` (PLAN.md §6);
- кэш: in-process (`core.cache`, TTL сутки) → при наличии сессии последняя строка
  `llm_call` со статусом `ok` и тем же хэшем; попадание пишется в журнал со статусом
  `cached` и нулём токенов. Ответы фолбэка (`fallback` — stub/rules вместо основной модели)
  не кэшируются и в replay не выгружаются: эвристика не должна подменять модель, когда
  та уже восстановилась;
- `DailyTokenCounter` — токены за календарный день (UTC) в памяти процесса; при первом
  вызове дня с сессией досеивается суммой из БД, чтобы рестарт не обнулял лимит.
  В лимит `LLM_DAILY_TOKEN_CAP` входят только платные провайдеры (`billable`): токены
  stub/rules/replay/cache считаются для статистики, но cap не расходуют; токены неудачных
  попыток (status=error) расходуют. Лимит проверяется до вызова провайдера —
  `LLMBudgetExceededError`;
- `request_json` хранит переменные после санитайзера, `template_hash`, число попыток и
  список провайдеров, которых пришлось пропустить (фолбэк); ПДн туда не попадают.
  Перед записью JSON нормализуется через `canonical_json` (UUID/Decimal/datetime → строки).

Сессия БД необязательна: CLI-инструменты и тесты работают с `session=None`, журнал тогда
живёт только в памяти (`recent`, счётчики) — `/llm/usage` показывает именно их. Все
обращения журнала к БД идут в SAVEPOINT (`begin_nested`): сбой журнала не переводит
бизнес-транзакцию вызывающего кода в состояние «rollback required».
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.cache import get_cache
from app.models.llm import LlmCall
from app.modules.llm.base import LLMBudgetExceededError

logger = logging.getLogger("app.llm.journal")

CACHE_PREFIX = "llm:"
CACHE_TTL_SECONDS = 24 * 3600
RECENT_LIMIT = 100
LATENCY_SAMPLES = 500
# провайдеры без оплаты за токены: их токены не расходуют LLM_DAILY_TOKEN_CAP
FREE_PROVIDERS = frozenset({"stub", "rules", "replay", "cache"})
# статусы, ответ которых можно отдавать из кэша и выгружать в replay
CACHEABLE_STATUSES = ("ok",)

# тариф ₽ за 1000 токенов (вход, выход) по моделям — заполняется после сверки тарифов
# в кабинете провайдера (PLAN.md §6, день 6); пока пусто → cost_est = NULL
MODEL_PRICES_RUB_PER_1K: dict[str, tuple[Decimal, Decimal]] = {}


# --- хэши ----------------------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Детерминированный JSON: сортировка ключей, без ASCII-escape, компактные разделители."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def input_hash(variables: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(variables).encode("utf-8")).hexdigest()


def compute_prompt_hash(
    kind: str, prompt_version: str, model: str, variables: dict[str, Any]
) -> str:
    """sha256(kind + prompt_version + model + вход) — ключ кэша ответов (PLAN.md §6)."""
    payload = "\n".join((kind, prompt_version, model, canonical_json(variables)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_json(value: Any) -> Any:
    """JSON-совместимая копия (UUID, Decimal, datetime → строки): JSONB-колонки
    принимают только то, что сериализует стандартный json."""
    if value is None:
        return None
    return json.loads(canonical_json(value))


def is_billable(provider: str) -> bool:
    return provider not in FREE_PROVIDERS


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> Decimal | None:
    prices = MODEL_PRICES_RUB_PER_1K.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    return (price_in * tokens_in + price_out * tokens_out) / Decimal(1000)


# --- счётчик дня ---------------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(UTC).date()


@dataclass
class DailyTokenCounter:
    """Токены, вызовы, статусы и латентности за текущий день (UTC) в памяти процесса.
    `tokens_*` — все токены (статистика), `billable_*` — только платных провайдеров (лимит)."""

    day: date = field(default_factory=_today)
    tokens_in: int = 0
    tokens_out: int = 0
    billable_in: int = 0
    billable_out: int = 0
    calls: int = 0
    by_kind: Counter[str] = field(default_factory=Counter)
    by_status: Counter[str] = field(default_factory=Counter)
    by_provider: Counter[str] = field(default_factory=Counter)
    tokens_by_kind: Counter[str] = field(default_factory=Counter)
    latencies_ms: deque[int] = field(default_factory=lambda: deque(maxlen=LATENCY_SAMPLES))
    seeded_day: date | None = None

    def _rollover(self) -> None:
        today = _today()
        if today != self.day:
            self.__init__()  # новый день — всё с нуля (seeded_day тоже)

    @property
    def tokens_total(self) -> int:
        self._rollover()
        return self.tokens_in + self.tokens_out

    @property
    def billable_total(self) -> int:
        self._rollover()
        return self.billable_in + self.billable_out

    def add(
        self,
        *,
        kind: str,
        status: str,
        provider: str,
        tokens_in: int,
        tokens_out: int,
        latency_ms: int,
        billable: bool | None = None,
    ) -> None:
        self._rollover()
        if billable is None:
            billable = is_billable(provider)
        self.tokens_in += max(0, tokens_in)
        self.tokens_out += max(0, tokens_out)
        if billable:
            self.billable_in += max(0, tokens_in)
            self.billable_out += max(0, tokens_out)
        self.calls += 1
        self.by_kind[kind] += 1
        self.by_status[status] += 1
        self.by_provider[provider] += 1
        self.tokens_by_kind[kind] += max(0, tokens_in) + max(0, tokens_out)
        self.latencies_ms.append(max(0, latency_ms))

    def seed(
        self,
        tokens_in: int,
        tokens_out: int,
        calls: int,
        billable_in: int | None = None,
        billable_out: int | None = None,
    ) -> None:
        """Досев из БД при старте дня: заменяет накопленное (БД — источник правды).
        Без `billable_*` платными считаются все переданные токены."""
        self._rollover()
        self.tokens_in = max(self.tokens_in, tokens_in)
        self.tokens_out = max(self.tokens_out, tokens_out)
        self.billable_in = max(self.billable_in, tokens_in if billable_in is None else billable_in)
        self.billable_out = max(
            self.billable_out, tokens_out if billable_out is None else billable_out
        )
        self.calls = max(self.calls, calls)
        self.seeded_day = self.day

    def check_cap(self, cap: int) -> None:
        """cap ≤ 0 — без лимита; иначе при исчерпании платных токенов — LLMBudgetExceededError."""
        if cap <= 0:
            return
        used = self.billable_total
        if used >= cap:
            raise LLMBudgetExceededError(
                f"дневной лимит токенов LLM исчерпан ({used} из {cap}); "
                "открытые ответы ждут следующего дня, закрытые задания оцениваются без ИИ"
            )

    def latency_percentiles(self) -> dict[str, int]:
        samples = sorted(self.latencies_ms)
        if not samples:
            return {"p50": 0, "p95": 0, "max": 0}

        def pct(p: float) -> int:
            index = min(len(samples) - 1, int(round((len(samples) - 1) * p)))
            return samples[index]

        return {"p50": pct(0.5), "p95": pct(0.95), "max": samples[-1]}

    def snapshot(self, cap: int) -> dict[str, Any]:
        self._rollover()
        total = self.tokens_in + self.tokens_out
        billable = self.billable_in + self.billable_out
        return {
            "day": self.day.isoformat(),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "tokens_total": total,
            "billable_total": billable,
            "daily_cap": cap,
            "remaining": (max(0, cap - billable) if cap > 0 else None),
            "cap_exhausted": cap > 0 and billable >= cap,
            "calls": self.calls,
            "by_kind": dict(self.by_kind),
            "by_status": dict(self.by_status),
            "by_provider": dict(self.by_provider),
            "tokens_by_kind": dict(self.tokens_by_kind),
            "latency_ms": self.latency_percentiles(),
            "seeded_from_db": self.seeded_day == self.day,
        }


# --- журнал --------------------------------------------------------------------------------------


@dataclass
class LlmCallRecord:
    """Данные одной строки `llm_call` (без ПДн)."""

    kind: str
    provider: str
    model: str
    prompt_version: str
    prompt_hash: str
    status: str  # ok | error | cached | fallback
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    request_json: dict[str, Any] = field(default_factory=dict)
    response_json: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_model(self) -> LlmCall:
        return LlmCall(
            kind=self.kind,
            provider=self.provider,
            model=self.model,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            tokens_in=self.tokens_in,
            tokens_out=self.tokens_out,
            latency_ms=self.latency_ms,
            cost_est=estimate_cost(self.model, self.tokens_in, self.tokens_out),
            status=self.status,
            request_json=self.request_json,
            response_json=self.response_json,
            error=self.error,
        )


class LlmJournal:
    """Запись вызовов, кэш по хэшу и дневной счётчик. Один экземпляр на процесс."""

    def __init__(self) -> None:
        self.counter = DailyTokenCounter()
        self.recent: deque[LlmCallRecord] = deque(maxlen=RECENT_LIMIT)
        self.db_errors = 0

    # --- кэш ---------------------------------------------------------------------------------
    async def lookup(self, session: AsyncSession | None, prompt_hash: str) -> dict[str, Any] | None:
        """Ответ по хэшу: память → БД (последняя строка со статусом ok и response_json)."""
        cached = await get_cache().get_json(CACHE_PREFIX + prompt_hash)
        if isinstance(cached, dict):
            return cached
        if session is None:
            return None
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(
                        select(LlmCall.response_json)
                        .where(
                            LlmCall.prompt_hash == prompt_hash,
                            LlmCall.status.in_(CACHEABLE_STATUSES),
                            LlmCall.response_json.is_not(None),
                        )
                        .order_by(LlmCall.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 — журнал не должен ронять вызов
            self.db_errors += 1
            logger.warning("llm_call: поиск в кэше не удался: %s", type(exc).__name__)
            return None
        if isinstance(row, dict):
            await get_cache().set_json(CACHE_PREFIX + prompt_hash, row, CACHE_TTL_SECONDS)
            return row
        return None

    async def remember(self, prompt_hash: str, response: dict[str, Any]) -> None:
        await get_cache().set_json(CACHE_PREFIX + prompt_hash, response, CACHE_TTL_SECONDS)

    # --- счётчик -----------------------------------------------------------------------------
    async def ensure_seeded(self, session: AsyncSession | None) -> None:
        """Раз в день (на процесс) подтянуть из БД сумму токенов за сегодня."""
        if session is None or self.counter.seeded_day == self.counter.day:
            return
        day_start = datetime.combine(self.counter.day, datetime.min.time(), tzinfo=UTC)
        billable = LlmCall.provider.not_in(FREE_PROVIDERS)
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(LlmCall.tokens_in), 0),
                            func.coalesce(func.sum(LlmCall.tokens_out), 0),
                            func.count(LlmCall.id),
                            func.coalesce(
                                func.sum(case((billable, LlmCall.tokens_in), else_=0)), 0
                            ),
                            func.coalesce(
                                func.sum(case((billable, LlmCall.tokens_out), else_=0)), 0
                            ),
                        ).where(
                            LlmCall.created_at >= day_start,
                            LlmCall.created_at < day_start + timedelta(days=1),
                        )
                    )
                ).one()
        except Exception as exc:  # noqa: BLE001
            self.db_errors += 1
            logger.warning("llm_call: досев дневного счётчика не удался: %s", type(exc).__name__)
            return
        self.counter.seed(int(row[0]), int(row[1]), int(row[2]), int(row[3]), int(row[4]))

    def check_budget(self, cap: int) -> None:
        self.counter.check_cap(cap)

    # --- запись ------------------------------------------------------------------------------
    async def record(self, session: AsyncSession | None, record: LlmCallRecord) -> LlmCall | None:
        """Учесть вызов в счётчиках, в `recent` и, при наличии сессии, в таблице `llm_call`
        (add + flush; commit — на вызывающей стороне, как и везде в проекте)."""
        self.counter.add(
            kind=record.kind,
            status=record.status,
            provider=record.provider,
            tokens_in=record.tokens_in,
            tokens_out=record.tokens_out,
            latency_ms=record.latency_ms,
        )
        self.recent.append(record)
        if record.status in CACHEABLE_STATUSES and record.response_json is not None:
            await self.remember(record.prompt_hash, record.response_json)
        if session is None:
            return None
        try:
            record.request_json = normalize_json(record.request_json) or {}
            record.response_json = normalize_json(record.response_json)
            row = record.to_model()
            async with session.begin_nested():
                session.add(row)
                await session.flush()
        except Exception as exc:  # noqa: BLE001 — журнал не должен ронять бизнес-вызов
            self.db_errors += 1
            # текст исключения не логируем: в нём могли бы оказаться переменные промпта
            logger.warning("llm_call: запись не удалась: %s", type(exc).__name__)
            return None
        return row

    # --- экспорт для replay -------------------------------------------------------------------
    async def export_replay(
        self, session: AsyncSession, path: Path, kinds: tuple[str, ...] | None = None
    ) -> int:
        """Выгрузить успешные вызовы в `llm_cache.jsonl` (основа `make llm-freeze`).
        Берутся строки со статусом ok (ответы фолбэка — эвристика stub — не выгружаются)
        с `request_json.variables`; по ключу (kind, version, вход) остаётся последняя.
        Возвращает число записей."""
        from app.modules.llm.providers.replay import ReplayRecord, reset_replay_cache

        stmt = (
            select(LlmCall)
            .where(LlmCall.status.in_(CACHEABLE_STATUSES), LlmCall.response_json.is_not(None))
            .order_by(LlmCall.created_at.asc())
        )
        if kinds:
            stmt = stmt.where(LlmCall.kind.in_(kinds))
        rows = (await session.execute(stmt)).scalars().all()
        records: dict[tuple[str, str, str], ReplayRecord] = {}
        for row in rows:
            variables = (row.request_json or {}).get("variables")
            if not isinstance(variables, dict) or row.response_json is None:
                continue
            record = ReplayRecord(
                kind=row.kind,
                prompt_version=row.prompt_version,
                input_hash=input_hash(variables),
                model=row.model,
                response=row.response_json,
                tokens_in=row.tokens_in,
                tokens_out=row.tokens_out,
                hash=row.prompt_hash,
            )
            records[record.key] = record
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for record in records.values():
                fh.write(canonical_json(record.to_json()) + "\n")
        reset_replay_cache()
        return len(records)

    def reset(self) -> None:
        self.counter = DailyTokenCounter()
        self.recent.clear()
        self.db_errors = 0


_journal: LlmJournal | None = None


def get_journal() -> LlmJournal:
    global _journal
    if _journal is None:
        _journal = LlmJournal()
    return _journal


def reset_journal() -> None:
    global _journal
    _journal = None


__all__ = [
    "CACHEABLE_STATUSES",
    "CACHE_PREFIX",
    "CACHE_TTL_SECONDS",
    "FREE_PROVIDERS",
    "MODEL_PRICES_RUB_PER_1K",
    "DailyTokenCounter",
    "LlmCallRecord",
    "LlmJournal",
    "canonical_json",
    "compute_prompt_hash",
    "estimate_cost",
    "get_journal",
    "input_hash",
    "is_billable",
    "normalize_json",
    "reset_journal",
]
