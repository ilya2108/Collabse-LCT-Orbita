"""GET /candidates/me/skill-states — персональный слой поверх общего графа навыков.

PostgreSQL нет: сессия подменяется `FakeSession` (порядок scalars: профиль → строки
candidate_skill_state). Пороги fresh/dim берутся из реального каталога (be/graph.yaml:
fresh 0.8, dim 0.5), состояние считается `api_node_state` из assessment/sessions.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.candidate import DEFAULT_PRIVACY, CandidateProfile, CandidateSkillState
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import fake_db

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


def _profile(user) -> CandidateProfile:
    return CandidateProfile(
        id=uuid.uuid4(),
        user_id=user.id,
        pseudonym="B-7F3A",
        privacy=dict(DEFAULT_PRIVACY),
        searchable=False,
        created_at=NOW,
        updated_at=NOW,
    )


def _state(profile: CandidateProfile, node_id: str, *, active_level: int, w_now: str):
    return CandidateSkillState(
        candidate_id=profile.id,
        node_id=node_id,
        active_level=active_level,
        strength_by_level={"1": float(w_now)},
        w_now=Decimal(w_now),
        freshest_at=NOW,
        computed_at=NOW,
    )


class TestAccess:
    def test_requires_candidate_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["employer"])), fake_db(app, FakeSession()):
            response = client.get("/api/v1/candidates/me/skill-states")
        assert response.status_code == 403 and response.json()["code"] == "forbidden"

    def test_without_profile_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        session = FakeSession(scalars=[None])  # профиля ещё нет
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/candidates/me/skill-states")
        assert response.status_code == 404, response.text
        assert response.json()["code"] == "not_found"


class TestSkillStates:
    def test_states_computed_by_thresholds(self, app: FastAPI, client: TestClient) -> None:
        """fresh — активен и w_now ≥ 0.8; fading — ≥ 0.5; expired — ниже dim;
        off — не активен или специализация узла не найдена в справочнике."""
        user = make_user(roles=["candidate"])
        profile = _profile(user)
        rows = [
            _state(profile, "be.B1", active_level=1, w_now="0.95"),  # fresh
            _state(profile, "be.B2", active_level=2, w_now="0.6"),  # fading
            _state(profile, "be.D1", active_level=1, w_now="0.3"),  # expired
            _state(profile, "be.D2", active_level=0, w_now="0.9"),  # off: не активен
            _state(profile, "zz.X1", active_level=3, w_now="0.99"),  # off: нет специализации
        ]
        session = FakeSession(scalars=[profile, rows])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/candidates/me/skill-states")
        assert response.status_code == 200, response.text
        states = response.json()["states"]
        by_node = {item["node_id"]: item for item in states}
        assert len(states) == 5
        assert by_node["be.B1"]["state"] == "fresh"
        assert by_node["be.B2"]["state"] == "fading"
        assert by_node["be.D1"]["state"] == "expired"
        assert by_node["be.D2"]["state"] == "off"
        assert by_node["zz.X1"]["state"] == "off"
        assert by_node["be.B1"]["specialization_code"] == "be"
        assert by_node["zz.X1"]["specialization_code"] == "zz"
        assert by_node["be.B1"]["active_level"] == 1
        assert by_node["be.B1"]["w_now"] == 0.95
        # лёгкий ответ без ПДн: только поля контракта
        assert set(by_node["be.B1"]) == {
            "node_id",
            "specialization_code",
            "active_level",
            "w_now",
            "state",
        }
        assert session.commits == 0 and session.added == []

    def test_no_rows_returns_empty_list(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        session = FakeSession(scalars=[_profile(user), []])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/candidates/me/skill-states")
        assert response.status_code == 200, response.text
        assert response.json() == {"states": []}
