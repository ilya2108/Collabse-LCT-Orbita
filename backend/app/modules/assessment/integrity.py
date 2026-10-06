"""Сигналы целостности тестовой сессии: агрегация телеметрии раннера.

Принцип ПДн-минимизации: в событиях нет текстов ответов — только виды событий,
тайминги и длины. Агрегат — простая и честно описанная формула (не «детектор списывания»,
а повод посмотреть внимательнее; финальное решение всегда за человеком):

- `large_paste`  — вставка из буфера длиной >= INTEGRITY_PASTE_LEN символов;
- `tab_blur`     — уход со вкладки (blur → следующий focus) на >= INTEGRITY_BLUR_SECONDS
                   секунд; blur без последующего focus тоже считается уходом;
- `fast_answer`  — ответ на задание быстрее INTEGRITY_MIN_SECONDS_PER_ITEM секунд
                   (длительность — `payload.duration_ms`, иначе разница client_ts
                   между item_shown и answer_submitted одного item_id);
- `no_edit`      — ответ длиной >= INTEGRITY_NO_EDIT_MIN_LEN при `chars_typed = 0`
                   («текст появился целиком, без набора»); без данных о наборе не флагуем.

Индекс: high — 0 флагов; medium — 1–2; low — 3+ (считаются виды флагов, не повторы).

Кэш агрегата — отдельная строка `test_session_integrity` (upsert при каждом пересчёте);
строка создаётся только при наличии событий: отсутствие строки в выдаче работодателя
означает `integrity_level = null` («данных нет»).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import TestSession, TestSessionEvent, TestSessionIntegrity
from app.modules.assessment.config import IntegritySettings

# порядок фиксирован: в нём флаги попадают в ответ API и в кэш
FLAG_TITLES: dict[str, str] = {
    "large_paste": "Крупные вставки из буфера",
    "tab_blur": "Уходы со вкладки",
    "fast_answer": "Слишком быстрые ответы",
    "no_edit": "Ответ без набора текста",
}


@dataclass
class IntegrityFlag:
    code: str
    title: str
    count: int
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"code": self.code, "title": self.title, "count": self.count, "detail": self.detail}


@dataclass
class IntegrityResult:
    level: str
    flags: list[IntegrityFlag] = field(default_factory=list)
    events_total: int = 0


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _payload_int(payload: dict | None, *keys: str) -> int | None:
    """Первое заполненное целое из payload по списку ключей (len → chars_pasted и т. п.)."""
    for key in keys:
        value = (payload or {}).get(key)
        if isinstance(value, int | float):
            return int(value)
    return None


def _large_paste(
    events: list[TestSessionEvent], settings: IntegritySettings
) -> IntegrityFlag | None:
    sizes = [
        size
        for e in events
        if e.kind == "paste"
        and (size := _payload_int(e.payload, "len", "chars_pasted")) is not None
        and size >= settings.integrity_paste_len
    ]
    if not sizes:
        return None
    return IntegrityFlag(
        code="large_paste",
        title=FLAG_TITLES["large_paste"],
        count=len(sizes),
        detail=f"Вставки от {settings.integrity_paste_len} символов: "
        f"{len(sizes)} шт., самая крупная — {max(sizes)}",
    )


def _tab_blur(events: list[TestSessionEvent], settings: IntegritySettings) -> IntegrityFlag | None:
    count = 0
    longest = 0.0
    blur_ts: datetime | None = None
    for e in events:  # events отсортированы по client_ts
        if e.kind == "blur" and blur_ts is None:
            blur_ts = _utc(e.client_ts)
        elif e.kind == "focus" and blur_ts is not None:
            gap = (_utc(e.client_ts) - blur_ts).total_seconds()
            if gap >= settings.integrity_blur_seconds:
                count += 1
                longest = max(longest, gap)
            blur_ts = None
    if blur_ts is not None:  # ушёл со вкладки и не вернулся
        count += 1
    if count == 0:
        return None
    detail = f"Уходы со вкладки от {settings.integrity_blur_seconds} с: {count}"
    if longest:
        detail += f", самый долгий — {round(longest)} с"
    return IntegrityFlag(code="tab_blur", title=FLAG_TITLES["tab_blur"], count=count, detail=detail)


def _fast_answer(
    events: list[TestSessionEvent], settings: IntegritySettings
) -> IntegrityFlag | None:
    shown: dict[str, datetime] = {}
    fast: list[str] = []
    for e in events:
        item_id = (e.payload or {}).get("item_id")
        if not isinstance(item_id, str):
            continue
        if e.kind == "item_shown":
            shown.setdefault(item_id, _utc(e.client_ts))
        elif e.kind == "answer_submitted":
            duration_ms = _payload_int(e.payload, "duration_ms")
            if duration_ms is not None:
                seconds: float | None = duration_ms / 1000
            elif item_id in shown:
                seconds = (_utc(e.client_ts) - shown[item_id]).total_seconds()
            else:
                seconds = None
            if seconds is not None and seconds < settings.integrity_min_seconds_per_item:
                fast.append(item_id)
    if not fast:
        return None
    return IntegrityFlag(
        code="fast_answer",
        title=FLAG_TITLES["fast_answer"],
        count=len(fast),
        detail=f"Ответы быстрее {settings.integrity_min_seconds_per_item} с: {len(fast)}",
    )


def _no_edit(events: list[TestSessionEvent], settings: IntegritySettings) -> IntegrityFlag | None:
    count = 0
    for e in events:
        if e.kind != "answer_submitted":
            continue
        length = _payload_int(e.payload, "len")
        typed = _payload_int(e.payload, "chars_typed")
        if length is not None and length >= settings.integrity_no_edit_min_len and typed == 0:
            count += 1
    if count == 0:
        return None
    return IntegrityFlag(
        code="no_edit",
        title=FLAG_TITLES["no_edit"],
        count=count,
        detail=f"Ответы от {settings.integrity_no_edit_min_len} символов "
        f"без единого набранного: {count}",
    )


def compute_integrity(
    events: Sequence[TestSessionEvent], settings: IntegritySettings
) -> IntegrityResult:
    """Чистая функция агрегации: события сессии → уровень и флаги (формула в докстринге)."""
    ordered = sorted(events, key=lambda e: _utc(e.client_ts))
    flags = [
        flag
        for check in (_large_paste, _tab_blur, _fast_answer, _no_edit)
        if (flag := check(ordered, settings)) is not None
    ]
    if len(flags) == 0:
        level = "high"
    elif len(flags) <= 2:
        level = "medium"
    else:
        level = "low"
    return IntegrityResult(level=level, flags=flags, events_total=len(ordered))


# --- работа с БД ------------------------------------------------------------------------


async def load_session_events(
    session: AsyncSession, session_id: uuid.UUID
) -> list[TestSessionEvent]:
    return list(
        (
            await session.execute(
                select(TestSessionEvent)
                .where(TestSessionEvent.session_id == session_id)
                .order_by(TestSessionEvent.client_ts, TestSessionEvent.received_at)
            )
        )
        .scalars()
        .all()
    )


async def recompute_session_integrity(
    session: AsyncSession,
    test_session: TestSession,
    settings: IntegritySettings,
    extra_events: Sequence[TestSessionEvent] = (),
) -> tuple[IntegrityResult, TestSessionIntegrity | None]:
    """Пересчёт агрегата и upsert кэша (commit — на вызывающей стороне).

    `extra_events` — только что принятые события, ещё не видимые в select (до flush).
    Порядок запросов фиксирован: события сессии → строка кэша (важно для тестов).
    Без событий строка кэша не создаётся (нет телеметрии — нет агрегата).
    """
    events = await load_session_events(session, test_session.id)
    known_ids = {e.id for e in events if e.id is not None}
    events.extend(e for e in extra_events if e.id is None or e.id not in known_ids)
    result = compute_integrity(events, settings)
    if result.events_total == 0:
        return result, None
    row = (
        await session.execute(
            select(TestSessionIntegrity).where(TestSessionIntegrity.session_id == test_session.id)
        )
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    flags_json = [f.as_dict() for f in result.flags]
    if row is None:
        row = TestSessionIntegrity(
            session_id=test_session.id,
            candidate_id=test_session.candidate_id,
            level=result.level,
            flags=flags_json,
            events_total=result.events_total,
            computed_at=now,
        )
        session.add(row)
    else:
        row.level = result.level
        row.flags = flags_json
        row.events_total = result.events_total
        row.computed_at = now
    return result, row


async def latest_integrity_by_candidate(
    session: AsyncSession, candidate_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """Уровень целостности последней ЗАВЕРШЁННОЙ сессии каждого кандидата (один запрос).

    Значение None — у последней завершённой сессии нет телеметрии; кандидат без
    завершённых сессий в словарь не попадает (в API обе ситуации — `null`, «данных нет»).
    """
    if not candidate_ids:
        return {}
    rows = (
        await session.execute(
            select(TestSession.candidate_id, TestSessionIntegrity.level)
            .outerjoin(TestSessionIntegrity, TestSessionIntegrity.session_id == TestSession.id)
            .where(
                TestSession.candidate_id.in_(list(candidate_ids)),
                TestSession.status == "finished",
            )
            .order_by(sa.desc(TestSession.finished_at).nulls_last())
        )
    ).all()
    levels: dict[uuid.UUID, str | None] = {}
    for candidate_id, level in rows:
        if candidate_id not in levels:
            levels[candidate_id] = level
    return levels
