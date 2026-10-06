"""Модуль employers: минимальный CRUD потребностей — зарплатный инвариант,
санитизация текста, изоляция по компании."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.employer import EmployerNeed
from app.models.skills import Specialization
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db


def need_row(company_id: uuid.UUID, **overrides) -> EmployerNeed:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "company_id": company_id,
        "title": "Backend-разработчик",
        "specialization_code": "be",
        "grade": "middle",
        "stack_tags": ["python"],
        "salary_from": 180_000,
        "salary_to": 260_000,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }
    return EmployerNeed(**{**base, **overrides})


def spec_row() -> Specialization:
    return Specialization(code="be", title="Бэкенд", status="active", graph_version="v1")


PAYLOAD = {
    "title": "Backend-разработчик (middle)",
    "specialization": "be",
    "grade": "middle",
    "salary_from": 180_000,
    "salary_to": 260_000,
    "stack_tags": ["Python", "python", "PostgreSQL"],
    "raw_text": "Нужен бэкендер, писать hr@corp.ru",
}


class TestCreateNeed:
    def test_salary_inverted_400(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post("/api/v1/needs", json={**PAYLOAD, "salary_from": 300_000})
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"

    def test_create_sanitizes_and_normalizes(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        session = FakeSession(scalars=[company], rows={(Specialization, "be"): spec_row()})
        with as_user(app, user), fake_db(app, session):
            response = client.post("/api/v1/needs", json=PAYLOAD)
        assert response.status_code == 201
        body = response.json()
        assert body["status"] == "active"
        assert body["stack_tags"] == ["python", "postgresql"]
        # контакты в тексте потребности заменены плейсхолдерами санитайзера
        assert "hr@corp.ru" not in body["raw_text"]
        assert body["salary_from"] == 180_000

    def test_unknown_specialization_400(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        session = FakeSession(scalars=[company_row(user)])
        with as_user(app, user), fake_db(app, session):
            response = client.post("/api/v1/needs", json={**PAYLOAD, "specialization": "ml"})
        assert response.status_code == 400


class TestReadUpdateNeed:
    def test_list(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        session = FakeSession(scalars=[company, [need_row(company.id)]])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/needs")
        assert response.status_code == 200
        assert response.json()["total"] == 1

    def test_foreign_need_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        session = FakeSession(scalars=[company_row(user), None])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_update_salary_invariant(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        session = FakeSession(scalars=[company, need])
        with as_user(app, user), fake_db(app, session):
            response = client.put(f"/api/v1/needs/{need.id}", json={"salary_from": 300_000})
        assert response.status_code == 400

    def test_update_ok(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        session = FakeSession(scalars=[company, need])
        with as_user(app, user), fake_db(app, session):
            response = client.put(
                f"/api/v1/needs/{need.id}", json={"status": "archived", "grade": "senior"}
            )
        assert response.status_code == 200
        assert need.status == "archived"
        assert need.grade == "senior"
