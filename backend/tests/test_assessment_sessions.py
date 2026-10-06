"""API сессий теста (контракт интегратора 11.10) через as_user + FakeSession, LLM — stub.

Полный happy-path демо-сессии junior `be`: POST /assessment/sessions → items/current →
answer по каждому заданию → finish (категория присвоена) → GET /candidates/me/category.
Плюс 409-ветки: profile_incomplete, cooldown_active, item_already_answered — и 404
для отсутствующей активной сессии. Порядок scalars в FakeSession повторяет порядок
запросов, задокументированный в `assessment/sessions.py`.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# модели под алиасами: имена Test* pytest пытается собрать как тест-классы
from app.models.assessment import GradeHistory
from app.models.assessment import TaskVariant as VariantModel
from app.models.assessment import TestItem as ItemModel
from app.models.assessment import TestSession as SessionModel
from app.models.candidate import CandidateCategory, CandidateSkillState, SkillConfirmation
from app.modules.assessment import sessions as engine
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_assessment_grading import catalog_of, template_def
from tests.test_employers_company import fake_db
from tests.test_matching_rank import candidate_profile_row

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# демо-профиль junior be: {be.B1, be.B2, be.D1}; B1/D1 — choice, B2 — text
JUNIOR_TEMPLATES = (
    template_def(node="be.B1", level=1, kind="mcq_reasoned", slug="api_one"),
    template_def(node="be.B2", level=1, kind="short_design", slug="api_two"),
    template_def(node="be.D1", level=1, kind="mcq_reasoned", slug="api_three"),
)
TEXT_ANSWER = "transaction isolation deadlock retry; index selectivity ordering"


@pytest.fixture()
def demo_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(engine, "template_catalog", lambda: catalog_of(*JUNIOR_TEMPLATES))


def profile_with_spec(user) -> object:
    return candidate_profile_row(
        user_id=user.id,
        specialization_code="be",
        stack_tags_confirmed=[],
        searchable=False,
    )


class TestCreateSession:
    def test_profile_incomplete_without_specialization(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id, specialization_code=None)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile])):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 409
        assert response.json()["code"] == "profile_incomplete"

    def test_cooldown_active(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        category = CandidateCategory(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            grade="junior",
            computed_at=datetime.now(UTC),
            grade_change_available_at=datetime.now(UTC) + timedelta(seconds=120),
        )
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, None, category])):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "middle"})
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == "cooldown_active"
        assert body["details"][0]["field"] == "next_attempt_at"

    def test_no_templates_available(
        self, app: FastAPI, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(engine, "template_catalog", lambda: catalog_of())
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, None, None])):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 409
        assert response.json()["code"] == "no_templates_available"

    def test_created_201_demo_junior(
        self, app: FastAPI, client: TestClient, demo_catalog: None
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        db = FakeSession(scalars=[profile, None, None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "active"
        assert body["specialization"] == "be"
        assert body["target_grade"] == "junior"
        assert body["items_total"] == 3
        assert body["current_index"] == 0
        session_row = db.added_of(SessionModel)[0]
        assert session_row.mode == "demo"
        assert session_row.details["demo"] is True
        assert len(session_row.details["draft_template_ids"]) == 3  # все фикстуры draft
        items = db.added_of(ItemModel)
        assert [item.node_id for item in items] == ["be.B1", "be.B2", "be.D1"]
        variants = db.added_of(VariantModel)
        assert all(v.retired_reason == "session_anchor" for v in variants)

    def test_existing_active_returned_200(
        self, app: FastAPI, client: TestClient, demo_catalog: None
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        active = SessionModel(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            specialization_code="be",
            target_grade="junior",
            mode="demo",
            status="in_progress",
            started_at=NOW,
        )
        item = ItemModel(
            id=uuid.uuid4(),
            session_id=active.id,
            candidate_id=profile.id,
            variant_id=uuid.uuid4(),
            node_id="be.B1",
            level=1,
            position=0,
            deadline_at=NOW,
            flags=[],
        )
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, active, [item]])):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 200
        assert response.json()["id"] == str(active.id)

    def test_current_404_without_active(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, None])):
            response = client.get("/api/v1/assessment/sessions/current")
        assert response.status_code == 404


class TestModes:
    """Режим сессии (`mode`): express — демо-ограничения (≤5), full — узлы полного
    T-профиля. Без monkeypatch каталога: реальные YAML-шаблоны app/skills (задача 1)."""

    def _create(self, app: FastAPI, client: TestClient, payload: dict) -> tuple[dict, FakeSession]:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        db = FakeSession(scalars=[profile, None, None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post("/api/v1/assessment/sessions", json=payload)
        assert response.status_code == 201, response.text
        return response.json(), db

    def test_express_capped_at_demo_limit(self, app: FastAPI, client: TestClient) -> None:
        body, db = self._create(app, client, {"target_grade": "junior", "mode": "express"})
        assert body["mode"] == "express"
        assert body["items_total"] <= engine.DEMO_MAX_ITEMS
        session_row = db.added_of(SessionModel)[0]
        assert session_row.details["mode"] == "express"
        assert session_row.details["demo"] is True
        # демо-профиль junior be: {B1, B2, D1}
        assert [i.node_id for i in db.added_of(ItemModel)] == ["be.B1", "be.B2", "be.D1"]

    def test_full_builds_item_per_target_node(self, app: FastAPI, client: TestClient) -> None:
        express, _ = self._create(app, client, {"target_grade": "junior", "mode": "express"})
        full, db = self._create(app, client, {"target_grade": "junior", "mode": "full"})
        assert full["mode"] == "full"
        assert full["items_total"] > express["items_total"]  # 4 узла T против 3 демо
        session_row = db.added_of(SessionModel)[0]
        assert session_row.details["mode"] == "full"
        assert session_row.details["demo"] is False
        assert session_row.mode == "full"
        # полный T-профиль junior be: {B1, B2, B3, D1}
        assert [i.node_id for i in db.added_of(ItemModel)] == ["be.B1", "be.B2", "be.B3", "be.D1"]

    def test_full_middle_covers_all_seven_nodes(self, app: FastAPI, client: TestClient) -> None:
        body, db = self._create(app, client, {"target_grade": "middle", "mode": "full"})
        assert body["items_total"] == 7  # все узлы T middle be обеспечены шаблонами L2
        session_row = db.added_of(SessionModel)[0]
        assert session_row.details["warnings"] == []
        assert all(i.level == 2 for i in db.added_of(ItemModel))

    def test_full_skips_nodes_without_templates_with_warning(
        self, app: FastAPI, client: TestClient
    ) -> None:
        body, db = self._create(app, client, {"target_grade": "senior", "mode": "full"})
        session_row = db.added_of(SessionModel)[0]
        nodes = [i.node_id for i in db.added_of(ItemModel)]
        # be.B5, be.D3, be.C3 пока без шаблонов — пропущены с warning, сессия собирается
        assert body["items_total"] == len(nodes) > 0
        for missing in ("be.B5", "be.D3", "be.C3"):
            assert missing not in nodes
            assert any(missing in warning for warning in session_row.details["warnings"])

    def test_no_mode_keeps_env_behaviour(self, app: FastAPI, client: TestClient) -> None:
        # conftest: TEST_DEMO_MODE=true → без mode сессия демо (экспресс), ≤5 заданий
        body, db = self._create(app, client, {"target_grade": "junior"})
        assert body["mode"] == "express"
        assert body["items_total"] <= engine.DEMO_MAX_ITEMS
        assert db.added_of(SessionModel)[0].details["demo"] is True

    def test_unknown_mode_rejected(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(
                "/api/v1/assessment/sessions",
                json={"target_grade": "junior", "mode": "marathon"},
            )
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"


class TestFullPath:
    """Сквозной путь: создание → задания → ответы → finish → категория."""

    def _create(self, app: FastAPI, client: TestClient, user, profile):
        db = FakeSession(scalars=[profile, None, None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post("/api/v1/assessment/sessions", json={"target_grade": "junior"})
        assert response.status_code == 201
        return (
            db.added_of(SessionModel)[0],
            db.added_of(ItemModel),
            db.added_of(VariantModel),
        )

    def test_happy_path_assigns_category(
        self, app: FastAPI, client: TestClient, demo_catalog: None
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row, items, variants = self._create(app, client, user, profile)
        variants_by_id = {variant.id: variant for variant in variants}

        # текущее задание: be.B1, choice с опциями и серверным таймером
        db = FakeSession(scalars=[profile, session_row, items, variants_by_id[items[0].variant_id]])
        with as_user(app, user), fake_db(app, db):
            response = client.get(f"/api/v1/assessment/sessions/{session_row.id}/items/current")
        assert response.status_code == 200
        body = response.json()
        assert body["index"] == 0 and body["total"] == 3
        assert body["item"]["kind"] == "choice"
        assert body["item"]["node_code"] == "be.B1"
        assert {o["id"] for o in body["item"]["options"]} == {"A", "B", "C", "D"}
        assert body["item"]["time_limit_seconds"] == 300
        assert engine.SERVED_FLAG in items[0].flags

        # ответы: choice — ключ «B», текст — по ключевым словам рубрики
        answers = [
            {"choice_id": "B"},
            {"text": TEXT_ANSWER},
            {"choice_id": "B"},
        ]
        for item, payload in zip(items, answers, strict=True):
            db = FakeSession(scalars=[profile, session_row, items, variants_by_id[item.variant_id]])
            with as_user(app, user), fake_db(app, db):
                response = client.post(
                    f"/api/v1/assessment/sessions/{session_row.id}/items/{item.id}/answer",
                    json=payload,
                )
            assert response.status_code == 200, response.text
            assert response.json()["accepted"] is True
        assert response.json() == {"accepted": True, "finished": True, "next_index": None}

        # повторный сабмит последнего задания → 409 item_already_answered
        db = FakeSession(scalars=[profile, session_row, items])
        with as_user(app, user), fake_db(app, db):
            response = client.post(
                f"/api/v1/assessment/sessions/{session_row.id}/items/{items[-1].id}/answer",
                json={"choice_id": "A"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == "item_already_answered"

        # finish: [профиль, сессия, items, варианты, подтверждения, снимки, категория, события]
        db = FakeSession(scalars=[profile, session_row, items, variants, [], [], None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "graded"
        assert body["grade"] == "junior"
        assert body["grade_confirmed"] is True
        assert body["q_level"] == 2  # {B1, B2, D1} связаны рёбрами графа be
        assert body["integrity"] is None  # телеметрии не было
        states = {node["code"]: node["state"] for node in body["nodes"]}
        assert states == {"be.B1": "fresh", "be.B2": "fresh", "be.D1": "fresh"}
        assert all(node["score"] == 1.0 for node in body["nodes"])

        assert session_row.status == "finished"
        assert session_row.outcome in ("passed", "strong")
        assert session_row.finished_at is not None
        confirmations = db.added_of(SkillConfirmation)
        assert {c.node_id for c in confirmations} == {"be.B1", "be.B2", "be.D1"}
        assert all(float(c.w0) == 0.85 for c in confirmations)  # 1.0 × demo_multiplier
        assert len(db.added_of(CandidateSkillState)) == 3
        category = db.added_of(CandidateCategory)[0]
        assert category.grade == "junior"
        assert category.q_level == 2
        assert category.source_session_id == session_row.id
        assert category.grade_change_available_at is not None
        history = db.added_of(GradeHistory)[0]
        assert history.outcome in ("passed", "strong")
        assert history.to_grade == "junior"
        assert profile.searchable is True  # privacy.searchable И категория есть
        assert "sql" in profile.stack_tags_confirmed

        # категория видна кандидату
        db = FakeSession(scalars=[profile, category])
        with as_user(app, user), fake_db(app, db):
            response = client.get("/api/v1/candidates/me/category")
        assert response.status_code == 200
        body = response.json()
        assert body["specialization"] == "be"
        assert body["grade"] == "junior"
        assert body["grade_confirmed"] is True

    def test_failed_session_gives_no_category(
        self, app: FastAPI, client: TestClient, demo_catalog: None
    ) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        session_row, items, variants = self._create(app, client, user, profile)
        variants_by_id = {variant.id: variant for variant in variants}
        # только be.B1 отвечен верно; остальные — неверно/мимо рубрики
        answers = [{"choice_id": "B"}, {"text": "не знаю"}, {"choice_id": "A"}]
        for item, payload in zip(items, answers, strict=True):
            db = FakeSession(scalars=[profile, session_row, items, variants_by_id[item.variant_id]])
            with as_user(app, user), fake_db(app, db):
                response = client.post(
                    f"/api/v1/assessment/sessions/{session_row.id}/items/{item.id}/answer",
                    json=payload,
                )
            assert response.status_code == 200
        db = FakeSession(scalars=[profile, session_row, items, variants, [], [], None, []])
        with as_user(app, user), fake_db(app, db):
            response = client.post(f"/api/v1/assessment/sessions/{session_row.id}/finish")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["grade"] is None
        assert body["grade_confirmed"] is False
        states = {node["code"]: node["state"] for node in body["nodes"]}
        assert states["be.B1"] == "fresh"
        assert states["be.D1"] == "off"
        assert db.added_of(CandidateCategory) == []
        assert session_row.outcome == "failed"
        history = db.added_of(GradeHistory)[0]
        assert history.outcome == "failed"
        assert history.to_grade is None

    def test_category_404_until_assigned(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = profile_with_spec(user)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[profile, None])):
            response = client.get("/api/v1/candidates/me/category")
        assert response.status_code == 404
        assert response.json()["code"] == "category_not_assigned"
