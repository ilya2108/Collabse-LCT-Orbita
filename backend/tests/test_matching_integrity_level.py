"""Модуль matching: бейдж `integrity_level` в выдаче работодателя —
`GET /needs/{id}/candidates` и `GET /needs/{id}/pipeline` (уровень от последней
завершённой сессии кандидата; null — телеметрии не было)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_matching_pipeline import entry_row
from tests.test_matching_rank import candidate_profile_row, category_row
from tests.test_needs_api import need_row


class TestNeedCandidatesIntegrity:
    def test_level_from_last_finished_session(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        category = category_row(profile.id)
        # компания → потребность → версия (нет) → профиль грейда (нет) → категории →
        # профили → уровни целостности (строки (candidate_id, level))
        session = FakeSession(
            scalars=[company, need, None, None, [category], [profile], [(profile.id, "low")]]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/candidates")
        assert response.status_code == 200
        assert response.json()["items"][0]["integrity_level"] == "low"

    def test_session_without_telemetry_gives_null(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        category = category_row(profile.id)
        # завершённая сессия есть, кэша целостности нет (outer join вернул NULL)
        session = FakeSession(
            scalars=[company, need, None, None, [category], [profile], [(profile.id, None)]]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/candidates")
        assert response.status_code == 200
        assert response.json()["items"][0]["integrity_level"] is None


class TestPipelineIntegrity:
    def test_level_in_pipeline_items(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row(pseudonym="B-1111")
        entry = entry_row(need.id, profile.id)
        # компания → потребность → строки воронки → версия → профили → уровни целостности
        # (приглашений в строках нет — запрос по ним не выполняется)
        session = FakeSession(
            scalars=[company, need, [entry], None, [profile], [(profile.id, "high")]]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/pipeline")
        assert response.status_code == 200
        columns = {c["stage"]: c["items"] for c in response.json()["columns"]}
        item = columns["shortlist"][0]
        assert item["integrity_level"] == "high"
        assert item["pseudonym"] == "B-1111"

    def test_no_sessions_gives_null(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        entry = entry_row(need.id, profile.id)
        session = FakeSession(scalars=[company, need, [entry], None, [profile], []])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/pipeline")
        assert response.status_code == 200
        columns = {c["stage"]: c["items"] for c in response.json()["columns"]}
        assert columns["shortlist"][0]["integrity_level"] is None
