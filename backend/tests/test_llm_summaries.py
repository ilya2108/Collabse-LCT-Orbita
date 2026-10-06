"""LLM-разборы человеческим языком: `GET /candidates/me/graph-summary` и
`GET /employers/needs/{id}/selection-summary`.

Rules-фолбэк проверяется мок-сервисом, бросающим исключение (как упавший провайдер):
эндпоинты не падают и отвечают детерминированным текстом `source="rules"`.
Happy-path LLM проверяется мок-сервисом без сети (со stub-провайдером он эквивалентен
фикстуре `graph_summary`/`selection_summary`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.assessment import TestSession as SessionRow
from app.models.candidate import CandidateSkillState
from app.models.skills import GradeProfile
from app.modules.llm.schemas import SummaryText
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_matching_rank import candidate_profile_row, category_row
from tests.test_needs_api import need_row


class _BrokenService:
    """LLM-сервис, который всегда падает — проверка rules-фолбэка."""

    async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        raise RuntimeError("LLM недоступен (тест)")


def break_llm(monkeypatch: pytest.MonkeyPatch, module: str) -> None:
    monkeypatch.setattr(f"{module}.get_llm_service", lambda: _BrokenService())


def skill_state_row(
    candidate_id: uuid.UUID, node_id: str, active_level: int, w_now: str
) -> CandidateSkillState:
    return CandidateSkillState(
        candidate_id=candidate_id,
        node_id=node_id,
        active_level=active_level,
        strength_by_level={},
        w_now=Decimal(w_now),
        computed_at=datetime.now(UTC),
    )


def finished_session_row(candidate_id: uuid.UUID) -> SessionRow:
    now = datetime.now(UTC)
    return SessionRow(
        id=uuid.uuid4(),
        candidate_id=candidate_id,
        specialization_code="be",
        target_grade="junior",
        status="finished",
        node_scores={"be.B1": 0.85, "be.B2": 0.55},
        outcome="passed",
        details={},
        started_at=now,
        finished_at=now,
    )


class TestGraphSummary:
    URL = "/api/v1/candidates/me/graph-summary"

    def test_requires_candidate_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["employer"])), fake_db(app, FakeSession()):
            assert client.get(self.URL).status_code == 403

    def test_404_without_profile(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession([None])):
            assert client.get(self.URL).status_code == 404

    def test_404_without_skill_states(self, app: FastAPI, client: TestClient) -> None:
        profile = candidate_profile_row(specialization_code="be")
        with (
            as_user(app, make_user(roles=["candidate"])),
            fake_db(app, FakeSession([profile, []])),
        ):
            assert client.get(self.URL).status_code == 404

    def test_rules_fallback_on_llm_error(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Исключение LLM-сервиса → детерминированный rules-фолбэк, не 5xx."""
        break_llm(monkeypatch, "app.modules.candidates.summary")
        profile = candidate_profile_row(specialization_code="be")
        states = [
            skill_state_row(profile.id, "be.B1", 2, "0.9"),  # fresh
            skill_state_row(profile.id, "be.B2", 1, "0.6"),  # fading
            skill_state_row(profile.id, "be.D4", 0, "0.1"),  # off
        ]
        category = category_row(profile.id, grade="junior")
        # профиль → снимки узлов → категория → последняя finished-сессия
        session = FakeSession([profile, states, category, finished_session_row(profile.id)])
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, session):
            response = client.get(self.URL)
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "rules"
        assert body["generated_at"]
        # три абзаца шаблона: сильная зона, затухание, что подтянуть до следующего грейда
        assert "Сильная зона" in body["summary"]
        assert "middle" in body["summary"]
        assert body["summary"].count("\n\n") == 2
        # ПДн и псевдонима в тексте нет
        assert profile.pseudonym not in body["summary"]

    def test_llm_success(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class _FakeResult:
            parsed = SummaryText(text="Разбор графа от модели.")

        class _FakeService:
            async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
                return _FakeResult()

        monkeypatch.setattr(
            "app.modules.candidates.summary.get_llm_service", lambda: _FakeService()
        )
        profile = candidate_profile_row(specialization_code="be")
        states = [skill_state_row(profile.id, "be.B1", 2, "0.9")]
        session = FakeSession([profile, states, category_row(profile.id), None])
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, session):
            response = client.get(self.URL)
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "llm"
        assert body["summary"] == "Разбор графа от модели."


def grade_profile_row() -> GradeProfile:
    return GradeProfile(
        id=uuid.uuid4(),
        specialization_code="be",
        grade="middle",
        target_nodes=["be.B1", "be.B2"],
        task_level=2,
        min_active=2,
        min_q=4,
        constraints={},
        budget={},
        demo={},
    )


class TestSelectionSummary:
    @staticmethod
    def url(need_id: uuid.UUID) -> str:
        return f"/api/v1/employers/needs/{need_id}/selection-summary"

    def test_requires_employer_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession()):
            assert client.get(self.url(uuid.uuid4())).status_code == 403

    def test_404_foreign_need(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        # компания → потребность не найдена (чужая/несуществующая)
        session = FakeSession([company_row(user), None])
        with as_user(app, user), fake_db(app, session):
            assert client.get(self.url(uuid.uuid4())).status_code == 404

    def test_404_without_subgraph(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        # компания → потребность → версия (нет) → профиль грейда (нет)
        session = FakeSession([company, need, None, None])
        with as_user(app, user), fake_db(app, session):
            assert client.get(self.url(need.id)).status_code == 404

    def test_rules_fallback_on_llm_error(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        break_llm(monkeypatch, "app.modules.matching.summary")
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)  # be / middle
        profile = candidate_profile_row()
        category = category_row(profile.id, grade="middle")
        states = [skill_state_row(profile.id, "be.B1", 2, "0.9")]  # be.B2 не закрыт
        # компания → потребность → версия (нет) → профиль грейда → категории → профили
        # → снимки узлов → уровни целостности
        session = FakeSession(
            [company, need, None, grade_profile_row(), [category], [profile], states, []]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get(self.url(need.id))
        assert response.status_code == 200
        body = response.json()
        assert body["source"] == "rules"
        assert body["generated_at"]
        assert "В выдаче 1 кандидатов" in body["summary"]
        # be.B2 никто не закрывает — кандидат на компромисс must → should
        assert "компромисс" in body["summary"]
        assert "Недобор" in body["summary"]
