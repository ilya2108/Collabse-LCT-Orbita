"""Модуль auth: `GET /auth/me`, выбор роли, согласие, удаление аккаунта, матрица permissions.

Роутеры тестируются через TestClient с подменой `get_current_user` (как `test_rbac.py`
«Альмы») и `get_session` (FakeSession) — без Keycloak и PostgreSQL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.db import get_session
from app.core.security import KNOWN_ROLES, SELECTABLE_ROLES
from app.models.audit import AuditLog
from app.models.candidate import CandidateProfile
from app.modules.auth.permissions import BASE_PERMISSIONS, ROLE_PERMISSIONS, permissions_for_roles
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession

ME_FIELDS = {
    "id",
    "email",
    "email_verified",
    "roles",
    "active_role",
    "consent",
    "created_at",
    "permissions",
}


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


def _audit_actions(session: FakeSession) -> list[str]:
    return [entry.action for entry in session.added_of(AuditLog)]


class TestMe:
    def test_requires_auth(self, client: TestClient) -> None:
        response = client.get("/api/v1/auth/me")
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "unauthorized" and body["request_id"]
        assert response.headers["X-Request-Id"] == body["request_id"]

    def test_me_contract_shape(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], email="Anna@example.com", consent=True)
        with as_user(app, user):
            response = client.get("/api/v1/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert set(body) == ME_FIELDS
        assert body["id"] == str(user.id)
        assert body["email"] == "Anna@example.com" and body["email_verified"] is True
        assert body["roles"] == ["candidate"] and body["active_role"] == "candidate"
        assert set(body["consent"]) == {"pdn_at", "policy_version", "publish_at"}
        assert body["consent"]["policy_version"] == "v1" and body["consent"]["pdn_at"]
        assert body["consent"]["publish_at"] is None
        assert "candidates:self" in body["permissions"]

    def test_first_login_without_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=[], consent=False)):
            body = client.get("/api/v1/auth/me").json()
        assert body["roles"] == [] and body["active_role"] is None
        assert body["consent"] == {"pdn_at": None, "policy_version": None, "publish_at": None}
        assert set(body["permissions"]) == set(BASE_PERMISSIONS)


class TestChooseRole:
    def test_sets_role_and_audits(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=[], consent=False)
        with as_user(app, user), fake_db(app, FakeSession()) as session:
            response = client.post("/api/v1/auth/role", json={"role": "employer"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["roles"] == ["employer"] and body["active_role"] == "employer"
        assert "needs:write" in body["permissions"]
        assert user.roles == ["employer"] and user.active_role == "employer"
        assert session.commits == 1 and _audit_actions(session) == ["role.selected"]
        audit = session.added_of(AuditLog)[0]
        assert audit.entity_type == "app_user" and audit.entity_id == user.id
        assert audit.after == {"roles": ["employer"], "active_role": "employer"}

    def test_second_choice_conflicts(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        with as_user(app, user), fake_db(app, FakeSession()) as session:
            response = client.post("/api/v1/auth/role", json={"role": "employer"})
        assert response.status_code == 409
        assert response.json()["code"] == "role_already_set"
        assert user.roles == ["candidate"] and session.commits == 0

    def test_admin_may_switch(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["admin", "candidate"], active_role="candidate")
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/role", json={"role": "employer"})
        assert response.status_code == 200
        body = response.json()
        assert body["roles"] == ["admin", "candidate", "employer"]
        assert body["active_role"] == "employer"

    @pytest.mark.parametrize("payload", [{"role": "admin"}, {"role": "kam"}, {}])
    def test_rejects_non_selectable(
        self, app: FastAPI, client: TestClient, payload: dict[str, Any]
    ) -> None:
        with as_user(app, make_user(roles=[])), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/role", json=payload)
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["details"][0]["field"] == "role"


class TestConsent:
    def test_consent_with_publish(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=False)
        with as_user(app, user), fake_db(app, FakeSession()) as session:
            response = client.post(
                "/api/v1/auth/consent", json={"policy_version": "2026-10-06", "publish": True}
            )
        assert response.status_code == 200, response.text
        consent = response.json()["consent"]
        assert consent["policy_version"] == "2026-10-06"
        assert consent["pdn_at"] and consent["publish_at"]
        assert _audit_actions(session) == ["consent.given"] and session.commits == 1
        audit = session.added_of(AuditLog)[0]
        assert audit.before["consent_pdn_at"] is None and audit.after["consent_pdn_at"]

    def test_publish_revoke_keeps_pdn_consent(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        user.consent_publish_at = user.consent_pdn_at
        with as_user(app, user), fake_db(app, FakeSession()) as session:
            response = client.post(
                "/api/v1/auth/consent", json={"policy_version": "v1", "publish": False}
            )
        consent = response.json()["consent"]
        assert consent["publish_at"] is None and consent["policy_version"] == "v1"
        assert datetime.fromisoformat(consent["pdn_at"]) == user.consent_pdn_at
        assert _audit_actions(session) == ["consent.updated"]

    def test_new_policy_version_refreshes_timestamp(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        old = user.consent_pdn_at
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/consent", json={"policy_version": "v2"})
        assert response.status_code == 200
        assert user.consent_policy_version == "v2" and user.consent_pdn_at > old

    def test_policy_version_required(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["candidate"])), fake_db(app, FakeSession()):
            response = client.post("/api/v1/auth/consent", json={"policy_version": ""})
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"


class TestDeleteAccount:
    def test_erases_pii_and_audits(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], email="anna@example.com", consent=True)
        user.email_hash = "a" * 64
        profile = CandidateProfile(
            id=user.id,
            user_id=user.id,
            pseudonym="B-7F3A",
            full_name="Иванова Анна",
            full_name_hash="b" * 64,
            phone="+79160001122",
            phone_hash="c" * 64,
            city="Москва",
            privacy={"searchable": True},
            searchable=True,
        )
        session = FakeSession(scalars=[profile, None])  # профиль есть, компании нет
        with as_user(app, user), fake_db(app, session):
            response = client.delete("/api/v1/auth/account")
        assert response.status_code == 204 and response.content == b""
        assert user.deleted_at is not None and user.email is None and user.email_hash is None
        assert user.username.startswith("deleted-")
        assert user.consent_pdn_at is None and user.consent_publish_at is None
        assert profile.full_name is None and profile.full_name_hash is None
        assert profile.phone is None and profile.phone_hash is None
        assert profile.city is None and profile.searchable is False
        assert profile.privacy["searchable"] is False
        assert _audit_actions(session) == ["account.deleted"] and session.commits == 1
        audit = session.added_of(AuditLog)[0]
        assert audit.after["candidate_profile_anonymized"] is True
        assert audit.after["company_contacts_erased"] is False


class TestPermissionsMatrix:
    def test_roles_cover_known_set(self) -> None:
        assert set(ROLE_PERMISSIONS) == set(KNOWN_ROLES)

    def test_base_permissions_everywhere(self) -> None:
        for perms in ROLE_PERMISSIONS.values():
            assert BASE_PERMISSIONS <= perms

    def test_candidate_and_employer_are_disjoint_beyond_base(self) -> None:
        shared = ROLE_PERMISSIONS["candidate"] & ROLE_PERMISSIONS["employer"]
        assert shared == BASE_PERMISSIONS

    def test_admin_is_superset(self) -> None:
        for role in SELECTABLE_ROLES:
            assert ROLE_PERMISSIONS[role] <= ROLE_PERMISSIONS["admin"]
        assert "admin:demo_reset" not in ROLE_PERMISSIONS["employer"]

    def test_contacts_reveal_is_employer_only(self) -> None:
        assert "contacts:reveal" in ROLE_PERMISSIONS["employer"]
        assert "contacts:reveal" not in ROLE_PERMISSIONS["candidate"]

    def test_union_sorted_and_unknown_ignored(self) -> None:
        merged = permissions_for_roles(["candidate", "unknown"])
        assert merged == sorted(ROLE_PERMISSIONS["candidate"])
        assert permissions_for_roles([]) == sorted(BASE_PERMISSIONS)
