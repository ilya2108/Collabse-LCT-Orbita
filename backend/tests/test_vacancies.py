"""Модуль vacancies: публичный список опубликованных вакансий, идемпотентный отклик
кандидата, вакансии своей компании со счётчиком откликов (FakeSession, без PostgreSQL)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.candidate import CandidateProfile
from app.models.employer import Application, NeedVersion, Vacancy
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db


def vacancy_row(company_id: uuid.UUID, **overrides) -> Vacancy:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "company_id": company_id,
        "need_id": None,
        "title": "Backend-разработчик (Junior)",
        "description": "FastAPI, PostgreSQL, тесты; наставник и код-ревью.",
        "specialization_code": "be",
        "grade": "junior",
        "salary_from": 90_000,
        "salary_to": 140_000,
        "format": "remote",
        "status": "published",
        "published_at": now,
        "created_at": now,
        "updated_at": now,
    }
    return Vacancy(**{**base, **overrides})


def profile_row(user) -> CandidateProfile:
    return CandidateProfile(id=uuid.uuid4(), user_id=user.id, pseudonym="B-7F3A")


def version_row(need_id: uuid.UUID, nodes_count: int = 4) -> NeedVersion:
    return NeedVersion(
        need_id=need_id,
        version=1,
        mode="verified",
        subgraph={"nodes": [{"id": f"be.B{i}", "min_level": 1} for i in range(nodes_count)]},
        filters={},
        result=[],
    )


class TestPublicList:
    def test_anonymous_401(self, client: TestClient) -> None:
        assert client.get("/api/v1/vacancies").status_code == 401

    def test_candidate_sees_published(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        company_id = uuid.uuid4()
        need_id = uuid.uuid4()
        vacancy = vacancy_row(company_id, need_id=need_id)
        # список (вакансия, имя компании) → версии подграфа → профиль кандидата (нет)
        session = FakeSession(
            scalars=[[(vacancy, "ФСП.Орбита Демо")], [version_row(need_id)], None]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/vacancies")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["company_name"] == "ФСП.Орбита Демо"
        assert item["need_nodes_count"] == 4
        assert item["applied"] is False
        assert item["salary_from"] == 90_000

    def test_applied_flag_for_candidate(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        vacancy = vacancy_row(uuid.uuid4())
        profile = profile_row(user)
        # список → (версий нет: need_id NULL) → профиль → id вакансий с откликом
        session = FakeSession(scalars=[[(vacancy, "Компания")], profile, [vacancy.id]])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/vacancies")
        assert response.status_code == 200
        assert response.json()["items"][0]["applied"] is True

    def test_employer_sees_list_too(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        vacancy = vacancy_row(uuid.uuid4())
        session = FakeSession(scalars=[[(vacancy, "Компания")], None])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/vacancies")
        assert response.status_code == 200
        assert response.json()["total"] == 1


class TestApply:
    def test_apply_creates_application(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        vacancy = vacancy_row(uuid.uuid4())
        profile = profile_row(user)
        # профиль существует → существующего отклика нет
        session = FakeSession(scalars=[profile, None], rows={(Vacancy, vacancy.id): vacancy})
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/vacancies/{vacancy.id}/apply")
        assert response.status_code == 200
        body = response.json()
        assert body["vacancy_id"] == str(vacancy.id)
        assert body["status"] == "sent"
        applications = session.added_of(Application)
        assert len(applications) == 1 and applications[0].candidate_id == profile.id
        assert session.commits == 1

    def test_apply_is_idempotent(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        vacancy = vacancy_row(uuid.uuid4())
        profile = profile_row(user)
        now = datetime.now(UTC)
        existing = Application(
            id=uuid.uuid4(),
            candidate_id=profile.id,
            vacancy_id=vacancy.id,
            status="sent",
            sent_at=now,
            created_at=now,
        )
        session = FakeSession(scalars=[profile, existing], rows={(Vacancy, vacancy.id): vacancy})
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/vacancies/{vacancy.id}/apply")
        assert response.status_code == 200
        assert response.json()["id"] == str(existing.id)
        assert session.added_of(Application) == []

    def test_apply_unpublished_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        vacancy = vacancy_row(uuid.uuid4(), status="draft")
        session = FakeSession(rows={(Vacancy, vacancy.id): vacancy})
        with as_user(app, user), fake_db(app, session):
            response = client.post(f"/api/v1/vacancies/{vacancy.id}/apply")
        assert response.status_code == 404

    def test_apply_requires_candidate_role(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(f"/api/v1/vacancies/{uuid.uuid4()}/apply")
        assert response.status_code == 403


class TestMine:
    def test_mine_lists_company_vacancies_with_counts(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        first = vacancy_row(company.id)
        second = vacancy_row(company.id, title="Backend-разработчик в команду платформы")
        # своя компания → вакансии компании → счётчики откликов (need_id нет — версии не нужны)
        session = FakeSession(scalars=[company, [first, second], [(first.id, 2)]])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/vacancies/mine")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        counts = {item["id"]: item["applications_count"] for item in body["items"]}
        assert counts[str(first.id)] == 2
        assert counts[str(second.id)] == 0

    def test_mine_requires_employer_role(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.get("/api/v1/vacancies/mine")
        assert response.status_code == 403
