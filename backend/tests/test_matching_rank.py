"""Модуль matching: честная заглушка ранжирования — покрытие подграфа, «полка»
неподтверждённого грейда ниже, объяснимость; `GET /needs/{id}/candidates`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.candidate import DEFAULT_PRIVACY, CandidateCategory, CandidateProfile
from app.modules.matching.service import (
    CandidateFacts,
    NeedNode,
    need_nodes_from_subgraph,
    node_match,
    rank_candidates,
)
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_needs_api import need_row


class TestPureRanking:
    def test_need_nodes_two_formats(self) -> None:
        nodes = need_nodes_from_subgraph(
            {"nodes": [{"id": "be.B1", "min_level": 2, "importance": "should"}]}
        )
        assert nodes == [NeedNode(id="be.B1", min_level=2, importance="should")]
        nodes = need_nodes_from_subgraph({"must": ["be.B1"], "nice": ["be.C1"]})
        assert {(n.id, n.importance) for n in nodes} == {("be.B1", "must"), ("be.C1", "nice")}
        assert need_nodes_from_subgraph(None) == []

    def test_node_match(self) -> None:
        assert node_match(2, 2) == 1.0
        assert node_match(3, 2) == 1.0
        assert node_match(1, 2) == 0.5
        assert node_match(0, 1) == 0.0
        assert node_match(1, 3) == 0.0

    def _facts(self, grade: str, levels: dict[str, int], q: int = 5) -> CandidateFacts:
        return CandidateFacts(
            candidate_id=uuid.uuid4(),
            pseudonym=f"B-{uuid.uuid4().hex[:4].upper()}",
            grade=grade,
            q_level=q,
            shape="T",
            strength=0.7,
            active_levels=levels,
        )

    def test_coverage_weighted_by_importance(self) -> None:
        nodes = [
            NeedNode("be.B1", 2, "must"),
            NeedNode("be.B2", 2, "must"),
            NeedNode("be.C1", 1, "nice"),
        ]
        full = self._facts("middle", {"be.B1": 2, "be.B2": 2, "be.C1": 1})
        no_nice = self._facts("middle", {"be.B1": 2, "be.B2": 2})
        items = rank_candidates(nodes, "middle", [no_nice, full])
        assert items[0].candidate_id == full.candidate_id
        assert items[0].score == 1.0
        # (1.0 + 1.0 + 0) / 2.3
        assert abs(items[1].score - 2.0 / 2.3) < 1e-3
        assert items[1].explanation.missing_nodes == ["be.C1"]

    def test_unconfirmed_grade_shelf_below(self) -> None:
        nodes = [NeedNode("be.B1", 1, "must")]
        junior_full = self._facts("junior", {"be.B1": 3})  # полный матч, но чужой грейд
        middle_partial = self._facts("middle", {})  # нулевое покрытие, свой грейд
        items = rank_candidates(nodes, "middle", [junior_full, middle_partial])
        assert items[0].candidate_id == middle_partial.candidate_id
        assert items[0].grade_confirmed is True
        assert items[1].grade_confirmed is False
        assert "ниже в выдаче" in items[1].explanation.text

    def test_without_nodes_sorts_by_strength(self) -> None:
        weak = self._facts("middle", {})
        weak.strength = 0.2
        strong = self._facts("middle", {})
        strong.strength = 0.9
        items = rank_candidates([], "middle", [weak, strong])
        assert items[0].candidate_id == strong.candidate_id
        assert items[0].coverage is None
        assert items[0].explanation.basis == "profile_strength"


def category_row(candidate_id: uuid.UUID, grade: str = "middle") -> CandidateCategory:
    return CandidateCategory(
        id=uuid.uuid4(),
        candidate_id=candidate_id,
        specialization_code="be",
        grade=grade,
        q_level=6,
        shape="T",
        strength=0.8,
        computed_at=datetime.now(UTC),
        history=[],
    )


def candidate_profile_row(**overrides) -> CandidateProfile:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "pseudonym": "B-7F3A",
        "privacy": dict(DEFAULT_PRIVACY),
        "searchable": True,
        "created_at": now,
        "updated_at": now,
    }
    return CandidateProfile(**{**base, **overrides})


class TestNeedCandidatesApi:
    def test_requires_employer(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession()):
            response = client.get(f"/api/v1/needs/{uuid.uuid4()}/candidates")
        assert response.status_code == 403

    def test_ranked_output_profile_strength_fallback(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        category = category_row(profile.id)
        # компания → потребность → версия (нет) → профиль грейда (нет) → категории → профили
        # → уровни целостности (нет завершённых сессий)
        session = FakeSession(scalars=[company, need, None, None, [category], [profile], []])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/candidates")
        assert response.status_code == 200
        body = response.json()
        assert body["basis"] == "profile_strength"
        assert body["total"] == 1
        item = body["items"][0]
        assert item["pseudonym"] == "B-7F3A"
        assert item["grade_confirmed"] is True
        # телеметрии сессий нет — бейдж целостности пустой («данных нет»)
        assert item["integrity_level"] is None
        # ПДн в выдаче нет
        assert "full_name" not in item

    def test_hidden_company_filtered_out(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row(
            privacy={**DEFAULT_PRIVACY, "hidden_company_ids": [str(company.id)]}
        )
        category = category_row(profile.id)
        session = FakeSession(scalars=[company, need, None, None, [category], [profile]])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/candidates")
        assert response.status_code == 200
        assert response.json()["total"] == 0
