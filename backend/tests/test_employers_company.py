"""Модуль employers: `GET|PUT /employers/me/company` — автосоздание личной компании
при первом обращении, частичное обновление, шифрование контактов, смена типа профиля.

PostgreSQL нет: сессия подменяется `FakeSession` (очередь ответов в порядке запросов
сервиса: своя компания → участие → счётчики).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.models.employer import Company, CompanyMember
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession


@contextmanager
def fake_db(app: FastAPI, session: FakeSession) -> Iterator[FakeSession]:
    previous = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = lambda: session
    try:
        yield session
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = previous


def company_row(user, **overrides) -> Company:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "user_id": user.id,
        "name": "ООО Тест",
        "kind": "personal",
        "created_at": now,
        "updated_at": now,
    }
    return Company(**{**base, **overrides})


class TestAccess:
    def test_requires_employer_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession()):
            response = client.get("/api/v1/employers/me/company")
        assert response.status_code == 403

    def test_anonymous_401(self, client: TestClient) -> None:
        assert client.get("/api/v1/employers/me/company").status_code == 401


class TestGetCompany:
    def test_first_call_creates_personal_company(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        # своя компания → None, участие → None, затем счётчик участников
        session = FakeSession(scalars=[None, None, 1])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/employers/me/company")
        assert response.status_code == 200
        body = response.json()
        assert body["kind"] == "personal"
        assert body["my_role"] == "owner"
        companies = session.added_of(Company)
        members = session.added_of(CompanyMember)
        assert len(companies) == 1 and companies[0].user_id == user.id
        assert len(members) == 1 and members[0].role == "owner" and members[0].status == "active"
        assert session.commits == 1

    def test_existing_owner_company(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, name="Орбита", kind="corporate")
        session = FakeSession(scalars=[company, 3])
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/employers/me/company")
        assert response.status_code == 200
        body = response.json()
        assert body["name"] == "Орбита"
        assert body["kind"] == "corporate"
        assert body["members_count"] == 3
        assert session.added == []

    def test_member_company(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(make_user(roles=["employer"]), kind="corporate")
        membership = CompanyMember(
            id=uuid.uuid4(),
            company_id=company.id,
            user_id=user.id,
            role="hr",
            status="active",
        )
        session = FakeSession(scalars=[None, membership, 2], rows={(Company, company.id): company})
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/employers/me/company")
        assert response.status_code == 200
        assert response.json()["my_role"] == "hr"


class TestUpdateCompany:
    def test_requires_consent(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"], consent=False)
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.put("/api/v1/employers/me/company", json={"name": "X"})
        assert response.status_code == 403
        assert response.json()["code"] == "consent_required"

    def test_updates_fields_and_encrypts_contacts(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        session = FakeSession(scalars=[company])
        payload = {
            "name": "ФСП.Орбита Демо",
            "kind": "corporate",
            "contact_email": "Hiring@Orbita.LOCAL",
            "contact_phone": "8 (999) 123-45-67",
        }
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/employers/me/company", json=payload)
        assert response.status_code == 200
        assert company.name == "ФСП.Орбита Демо"
        assert company.kind == "corporate"
        # контакты нормализованы, blind index заполнен сервисом
        assert company.contact_email == "hiring@orbita.local"
        assert company.contact_phone == "+79991234567"
        assert len(company.contact_email_hash) == 64
        assert len(company.contact_phone_hash) == 64

    def test_corporate_to_personal_blocked_with_members(
        self, app: FastAPI, client: TestClient
    ) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user, kind="corporate")
        # своя компания → счётчик активных участников (2 > 1 — нельзя)
        session = FakeSession(scalars=[company, 2])
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/employers/me/company", json={"kind": "personal"})
        assert response.status_code == 409
        assert response.json()["code"] == "company_has_members"

    def test_member_without_manager_role_forbidden(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(make_user(roles=["employer"]), kind="corporate")
        membership = CompanyMember(
            id=uuid.uuid4(),
            company_id=company.id,
            user_id=user.id,
            role="hiring_manager",
            status="active",
        )
        session = FakeSession(scalars=[None, membership], rows={(Company, company.id): company})
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/employers/me/company", json={"name": "Z"})
        assert response.status_code == 403

    def test_bad_inn_rejected(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.put("/api/v1/employers/me/company", json={"inn": "12AB"})
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"
