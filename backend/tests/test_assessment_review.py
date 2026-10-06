"""Идемпотентный finish и разбор последней сессии (`GET /assessment/sessions/last/review`).

Повторный finish завершённой сессии возвращает сохранённый в `details["result"]` итог
(200, байт-в-байт тот же ответ); у старых сессий без result итог реконструируется из
node_scores + candidate_skill_state; параллельный finish при stage=grading получает 409
`grading_in_progress` (фронт ретраит по коду). Review отдаёт тексты критериев рубрики,
попадания и feedback_short, но не раскрывает эталоны и ключи choice (банк закрытый).
Порядок scalars в FakeSession повторяет порядок запросов из `assessment/sessions.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# модели под алиасами: имена Test* pytest пытается собрать как тест-классы
from app.models.assessment import TaskVariant as VariantModel
from app.models.assessment import TestItem as ItemModel
from app.models.assessment import TestSession as SessionModel
from app.models.candidate import CandidateCategory, CandidateSkillState
from app.modules.assessment import sessions as engine
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_assessment_grading import catalog_of, template_def
from tests.test_employers_company import fake_db
from tests.test_matching_rank import candidate_profile_row

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# демо-профиль junior be: {be.B1, be.B2, be.D1}; B1/D1 — choice, B2 — text
REVIEW_TEMPLATES = (
    template_def(node="be.B1", level=1, kind="mcq_reasoned", slug="review_one"),
    template_def(node="be.B2", level=1, kind="short_design", slug="review_two"),
    template_def(node="be.D1", level=1, kind="mcq_reasoned", slug="review_three"),
)
TEXT_ANSWER = "transaction isolation deadlock retry; index selectivity ordering"
RUBRIC_FIRST_TEXT = "transaction isolation deadlock retry"  # текст критерия из template_def


@pytest.fixture()
def demo_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "template_catalog", lambda: catalog_of(*REVIEW_TEMPLATES))


def profile_with_spec(user) -> object:
    return candidate_profile_row(
        user_id=user.id,
        specialization_code="be",
        stack_tags_confirmed=[],
        searchable=False,
    )


def finished_session(profile) -> tuple[SessionModel, list[ItemModel], list[VariantModel]]:
    """Завершённая демо-сессия с сохранённым result: choice (верно), text (0.75,
    эвристика llm_fallback), пропущенное задание."""
    session = SessionModel(
        id=uuid.uuid4(),
        candidate_id=profile.id,
        specialization_code="be",
        target_grade="junior",
        mode="demo",
        kind="initial",
        status="finished",
        stage="done",
        outcome="passed",
        node_scores={"be.B1": 1.0, "be.B2": 0.75, "be.D1": 0.0},
        details={
            "demo": True,
            "mode": "express",
            "result": {
                "grade": "junior",
                "grade_confirmed": True,
                "q_level": 2,
                "nodes": [
                    {"code": "be.B1", "title": "Узел B1", "score": 1.0, "state": "fresh"},
                    {"code": "be.B2", "title": "Узел B2", "score": 0.75, "state": "fresh"},
                    {"code": "be.D1", "title": "Узел D1", "score": 0.0, "state": "off"},
                ],
                "integrity": None,
            },
        },
        started_at=NOW,
        finished_at=NOW,
    )
    gradings: list[dict[str, Any]] = [
        {"kind": "choice", "correct": True},
        {
            "kind": "text",
            "criteria": [
                {"id": "first", "hit": 1, "evidence": "транзакции и дедлоки"},
                {"id": "second", "hit": 0.5, "evidence": ""},
            ],
            "score": 0.75,
            "flags": [],
            "feedback_short": "Раскройте подробнее выбор индексов.",
        },
        {"kind": "skipped"},
    ]
    scores = [Decimal("1"), Decimal("0.75"), Decimal("0")]
    flags = [[], [engine.LLM_FALLBACK_FLAG], [engine.UNANSWERED_FLAG]]
    answered = [NOW, NOW, None]
    items: list[ItemModel] = []
    variants: list[VariantModel] = []
    for position, template in enumerate(REVIEW_TEMPLATES):
        variant = VariantModel(
            id=uuid.uuid4(),
            template_id=uuid.uuid4(),
            payload=engine.anchor_payload(template),
            content_hash="f" * 64,
            status="retired",
            retired_reason="session_anchor",
        )
        variants.append(variant)
        items.append(
            ItemModel(
                id=uuid.uuid4(),
                session_id=session.id,
                candidate_id=profile.id,
                variant_id=variant.id,
                node_id=template.node,
                level=1,
                position=position,
                served_at=NOW,
                deadline_at=NOW,
                answered_at=answered[position],
                score=scores[position],
                grading=gradings[position],
                flags=flags[position],
            )
        )
    return session, items, variants


class TestFinishIdempotent:
    """Повторный и конкурентный finish: сохранённый итог, реконструкция, 409 при гонке."""

    def _run_session(self, app: FastAPI, client: TestClient, user, profile):
        db = FakeSession(scalars=[profile, None, None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 201
        session_row = db.added_of(SessionModel)[0]
        items = db.added_of(ItemModel)
        variants = db.added_of(VariantModel)
        variants_by_id = {variant.id: variant for variant in variants}
        answers = [{"choice_id": "B"}, {"text": TEXT_ANSWER}, {"choice_id": "B"}]
        for item, payload in zip(items, answers, strict=True):
            db = FakeSession(scalars=[profile, session_row, items, variants_by_id[item.variant_id]])
            with as_user(app, user), fake_db(app, db):
                response = client.post(
                    f"/api/v1/assessment/sessions/{session_row.id}/items/{item.id}/answer",
                    json=payload,
                )
            assert response.status_code == 200, response.text
        return session_row, items, variants

    def test_repeat_finish_returns_saved_result(
        self, app: FastAPI, client: TestClient, demo_catalog: None
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row, items, variants = self._run_session(app, client, user, profile)

        # finish №1: [профиль, сессия, items, варианты, подтверждения, снимки, категория, события]
        db = FakeSession(scalars=[profile, session_row, items, variants, [], [], None, []])
        with as_user(app, user), fake_db(app, db):
            first = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert first.status_code == 200, first.text
        assert session_row.status == "finished"
        assert session_row.stage == "done"
        saved = session_row.details["result"]
        assert saved["grade"] == "junior"
        assert saved["grade_confirmed"] is True
        assert [node["code"] for node in saved["nodes"]] == ["be.B1", "be.B2", "be.D1"]

        # finish №2 (ретрай фронта после таймаута прокси): [профиль, сессия] — 200, тот же итог
        db = FakeSession(scalars=[profile, session_row])
        with as_user(app, user), fake_db(app, db):
            second = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert second.status_code == 200, second.text
        assert second.json() == first.json()

    def test_conflict_while_grading_in_progress(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row = SessionModel(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            target_grade="junior",
            mode="demo",
            status="in_progress",
            stage=engine.STAGE_GRADING,
            started_at=NOW,
        )
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, session_row])):
            response = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "grading_in_progress"
        assert "Оценка" in body["message"]

    def test_legacy_finished_session_reconstructed(self, app: FastAPI, client: TestClient) -> None:
        """Сессия, завершённая до релиза сохранённого result: grade/q_level/nodes
        реконструируются из outcome, категории, node_scores и candidate_skill_state."""
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row = SessionModel(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            target_grade="junior",
            mode="demo",
            status="finished",
            stage="done",
            outcome="passed",
            node_scores={"be.B1": 1.0, "be.B2": 0.6, "be.D1": 0.0},
            details={"demo": True, "mode": "express"},
            started_at=NOW,
            finished_at=NOW,
        )
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            grade="junior",
            q_level=2,
            computed_at=NOW,
        )
        state_b1 = CandidateSkillState(
            candidate_id=profile.id,
            node_id="be.B1",
            active_level=1,
            strength_by_level={"1": 0.85},
            w_now=Decimal("0.85"),
            computed_at=NOW,
        )
        # [профиль, сессия, категория, снимки узлов, события телеметрии]
        db = FakeSession(scalars=[profile, session_row, category, [state_b1], []])
        with as_user(app, user), fake_db(app, db):
            response = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["grade"] == "junior"
        assert body["grade_confirmed"] is True
        assert body["q_level"] == 2
        assert body["integrity"] is None
        states = {node["code"]: node["state"] for node in body["nodes"]}
        assert states == {"be.B1": "fresh", "be.B2": "off", "be.D1": "off"}
        scores = {node["code"]: node["score"] for node in body["nodes"]}
        assert scores == {"be.B1": 1.0, "be.B2": 0.6, "be.D1": 0.0}


class TestLastSessionReview:
    """Разбор последней завершённой сессии: критерии с текстами рубрики, feedback_short,
    закрытый банк (эталоны и ключи не отдаются), 404 без завершённых сессий."""

    def test_review_returns_rubric_texts_and_feedback(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row, items, variants = finished_session(profile)
        # [профиль, последняя завершённая сессия, items, варианты]
        db = FakeSession(scalars=[profile, session_row, items, variants])
        with as_user(app, user), fake_db(app, db):
            response = client.get("/api/v1/assessment/sessions/last/review")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["session_id"] == str(session_row.id)
        assert body["mode"] == "express"
        assert body["target_grade"] == "junior"
        assert body["outcome"] == "passed"
        assert [node["code"] for node in body["nodes"]] == ["be.B1", "be.B2", "be.D1"]

        choice, text, skipped = body["items"]
        assert choice["kind"] == "choice"
        assert choice["correct"] is True
        assert choice["answered"] is True
        assert choice["criteria"] == []
        assert choice["llm_fallback"] is False

        assert text["kind"] == "text"
        assert text["node_code"] == "be.B2"
        assert text["score"] == 0.75
        assert text["llm_fallback"] is True
        assert text["feedback_short"] == "Раскройте подробнее выбор индексов."
        first, second = text["criteria"]
        assert first["text"] == RUBRIC_FIRST_TEXT  # текст критерия поднят из рубрики варианта
        assert first["hit"] == 1.0
        assert first["evidence"] == "транзакции и дедлоки"
        assert second["hit"] == 0.5

        assert skipped["kind"] == "skipped"
        assert skipped["answered"] is False
        assert skipped["correct"] is None
        assert skipped["score"] == 0.0

        # банк заданий закрытый: эталоны и ключи choice наружу не уходят
        assert "answer_key" not in response.text
        assert "reference_answer" not in response.text
        assert "Эталонный ответ" not in response.text

    def test_review_404_without_finished_sessions(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, None])):
            response = client.get("/api/v1/assessment/sessions/last/review")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "not_found"
        assert body["message"] == "Завершённых сессий нет"
