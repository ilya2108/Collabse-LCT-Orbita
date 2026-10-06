"""Банк кандидатов (`GET /matching/bank`): роль employer, фильтры по специализации
и грейду, видимость (searchable + скрытие от компании), сортировка и пагинация."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.candidate import DEFAULT_PRIVACY
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_matching_rank import candidate_profile_row, category_row

BANK_URL = "/api/v1/matching/bank"


class TestBankApi:
    def test_candidate_role_forbidden(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession()):
            response = client.get(BANK_URL)
        assert response.status_code == 403

    def test_lists_seed_candidates_without_pii(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        profile = candidate_profile_row(stack_tags_confirmed=["Python", "FastAPI"])
        category = category_row(profile.id)
        # компания → категории → профили
        session = FakeSession(scalars=[company, [category], [profile]])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL)
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["limit"] == 25
        assert body["offset"] == 0
        item = body["items"][0]
        assert item["pseudonym"] == "B-7F3A"
        assert item["specialization_code"] == "be"
        assert item["grade"] == "middle"
        assert item["q_level"] == 6
        assert item["shape"] == "T"
        assert item["stack_confirmed"] == ["Python", "FastAPI"]
        assert item["grade_confirmed_at"] is not None
        # ПДн и контактов в выдаче нет
        assert "full_name" not in item
        assert "phone" not in item
        assert "email" not in item

    def test_not_searchable_hidden(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        profile = candidate_profile_row(searchable=False)
        category = category_row(profile.id)
        session = FakeSession(scalars=[company, [category], [profile]])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL)
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_hidden_from_company_filtered_out(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        profile = candidate_profile_row(
            privacy={**DEFAULT_PRIVACY, "hidden_company_ids": [str(company.id)]}
        )
        category = category_row(profile.id)
        session = FakeSession(scalars=[company, [category], [profile]])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL)
        assert response.status_code == 200
        assert response.json()["total"] == 0

    def test_filters_applied_to_query(self, app: FastAPI, client: TestClient) -> None:
        """Фильтры уходят в WHERE запроса категорий (специализация и грейд)."""
        user = make_user(roles=["employer"])
        company = company_row(user)
        session = FakeSession(scalars=[company, []])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL, params={"specialization": "be", "grade": "senior"})
        assert response.status_code == 200
        assert response.json()["total"] == 0
        categories_sql = str(session.statements[1])
        assert "candidate_category.specialization_code =" in categories_sql
        assert "candidate_category.grade =" in categories_sql

    def test_sorted_by_grade_then_q_level(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        now = datetime.now(UTC)
        junior_profile = candidate_profile_row(pseudonym="B-JUN1")
        senior_profile = candidate_profile_row(id=uuid.uuid4(), pseudonym="B-SEN1")
        middle_profile = candidate_profile_row(id=uuid.uuid4(), pseudonym="B-MID9")
        junior = category_row(junior_profile.id, grade="junior")
        junior.q_level = 9
        senior = category_row(senior_profile.id, grade="senior")
        senior.q_level = 2
        middle = category_row(middle_profile.id, grade="middle")
        middle.q_level = 9
        for category in (junior, senior, middle):
            category.computed_at = now
        profiles = [junior_profile, senior_profile, middle_profile]
        session = FakeSession(scalars=[company, [junior, senior, middle], profiles])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL)
        assert response.status_code == 200
        pseudonyms = [item["pseudonym"] for item in response.json()["items"]]
        # senior выше middle выше junior, даже при меньшем q_level
        assert pseudonyms == ["B-SEN1", "B-MID9", "B-JUN1"]

    def test_pagination_envelope(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        first_profile = candidate_profile_row(pseudonym="B-AAAA")
        second_profile = candidate_profile_row(id=uuid.uuid4(), pseudonym="B-BBBB")
        first = category_row(first_profile.id, grade="senior")
        second = category_row(second_profile.id, grade="junior")
        session = FakeSession(scalars=[company, [first, second], [first_profile, second_profile]])
        with as_user(app, user), fake_db(app, session):
            response = client.get(BANK_URL, params={"limit": 1, "offset": 1})
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["limit"] == 1
        assert body["offset"] == 1
        assert [item["pseudonym"] for item in body["items"]] == ["B-BBBB"]
