"""Сид демо-сигналов целостности: 2–3 завершённые сессии теста с телеметрией.

Запускается после `matching`-сида (профили демо-кандидатов уже есть) и даёт жюри
бейдж целостности в выдаче работодателя без прохождения теста: у демо-кандидатов
middle-потребности появляются уровни high / medium / low. События — только длины
и тайминги (как в боевом контракте), агрегат считается боевой функцией
`compute_integrity` и кэшируется в `test_session_integrity`.

Идемпотентен: кандидат с уже существующей seed-сессией пропускается.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import TestSession, TestSessionEvent, TestSessionIntegrity
from app.models.candidate import CandidateProfile
from app.models.user import AppUser
from app.modules.assessment.config import get_integrity_settings
from app.modules.assessment.integrity import compute_integrity

# suffix демо-кандидата (matching-сид) → сценарий телеметрии
DEMO_SCENARIOS: tuple[tuple[str, str], ...] = (
    ("cand-12", "high"),
    ("cand-13", "medium"),
    ("cand-14", "low"),
)


def _events_for(scenario: str, started: datetime) -> list[dict[str, Any]]:
    """События сценария (kind, смещение от старта в секундах, payload)."""

    def ev(kind: str, offset: float, **payload: Any) -> dict[str, Any]:
        return {"kind": kind, "client_ts": started + timedelta(seconds=offset), "payload": payload}

    if scenario == "high":
        # спокойное прохождение: думал, печатал, мелкая вставка — ни одного флага
        return [
            ev("item_shown", 0, item_id="i1"),
            ev("first_input", 12, item_id="i1"),
            ev("paste", 40, item_id="i1", len=60),
            ev("answer_submitted", 95, item_id="i1", len=340, chars_typed=300, duration_ms=95_000),
            ev("item_shown", 100, item_id="i2"),
            ev("first_input", 115, item_id="i2"),
            ev(
                "answer_submitted",
                210,
                item_id="i2",
                len=280,
                chars_typed=280,
                duration_ms=110_000,
            ),
        ]
    if scenario == "medium":
        # 2 флага: крупная вставка + уход со вкладки
        return [
            ev("item_shown", 0, item_id="i1"),
            ev("blur", 10),
            ev("focus", 45),
            ev("first_input", 50, item_id="i1"),
            ev("paste", 60, item_id="i1", len=420, chars_pasted=420),
            ev("answer_submitted", 150, item_id="i1", len=430, chars_typed=25, duration_ms=150_000),
        ]
    # low: 4 флага — вставка, уход, слишком быстрый ответ, ответ без набора
    return [
        ev("item_shown", 0, item_id="i1"),
        ev("blur", 2),
        ev("focus", 55),
        ev("paste", 60, item_id="i1", len=800, chars_pasted=800),
        ev("answer_submitted", 66, item_id="i1", len=790, chars_typed=0, duration_ms=6_000),
        ev("item_shown", 70, item_id="i2"),
        ev("answer_submitted", 78, item_id="i2", len=260, chars_typed=0, duration_ms=8_000),
    ]


async def _ensure_session(session: AsyncSession, profile: CandidateProfile, scenario: str) -> bool:
    existing = (
        await session.execute(
            select(TestSession).where(
                TestSession.candidate_id == profile.id, TestSession.mode == "seed"
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    now = datetime.now(UTC)
    started = now - timedelta(days=2)
    test_session = TestSession(
        candidate_id=profile.id,
        specialization_code="be",
        target_grade="middle",
        mode="seed",
        kind="initial",
        status="finished",
        stage="done",
        outcome="passed",
        started_at=started,
        expires_at=started + timedelta(hours=2),
        finished_at=started + timedelta(minutes=40),
    )
    session.add(test_session)
    await session.flush()
    events = [
        TestSessionEvent(session_id=test_session.id, **spec)
        for spec in _events_for(scenario, started)
    ]
    session.add_all(events)
    result = compute_integrity(events, get_integrity_settings())
    session.add(
        TestSessionIntegrity(
            session_id=test_session.id,
            candidate_id=profile.id,
            level=result.level,
            flags=[f.as_dict() for f in result.flags],
            events_total=result.events_total,
            computed_at=now,
        )
    )
    return True


async def seed(session: AsyncSession) -> dict[str, int]:
    """Создаёт демо-сессии с телеметрией; возвращает счётчик для ответа API."""
    counters = {"sessions": 0}
    for suffix, scenario in DEMO_SCENARIOS:
        user = (
            await session.execute(
                select(AppUser).where(AppUser.keycloak_sub == f"seed:demo-{suffix}")
            )
        ).scalar_one_or_none()
        if user is None:
            continue  # matching-сид не отработал — пропускаем молча (демо-данные)
        profile = (
            await session.execute(
                select(CandidateProfile).where(CandidateProfile.user_id == user.id)
            )
        ).scalar_one_or_none()
        if profile is None:
            continue
        counters["sessions"] += int(await _ensure_session(session, profile, scenario))
    return counters
