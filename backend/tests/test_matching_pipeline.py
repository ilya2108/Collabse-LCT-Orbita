"""Модуль matching: мини-CRM — правила переходов стадий (юниты) и роутеры
`GET|POST /needs/{id}/pipeline`, `PATCH /needs/{id}/pipeline/{candidate_id}`."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.audit import AuditLog
from app.models.employer import Invitation, NeedPipelineEntry, PipelineStageEvent
from app.modules.matching.service import can_transition
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import company_row, fake_db
from tests.test_matching_rank import candidate_profile_row
from tests.test_needs_api import need_row


def entry_row(need_id: uuid.UUID, candidate_id: uuid.UUID, **overrides) -> NeedPipelineEntry:
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "need_id": need_id,
        "candidate_id": candidate_id,
        "stage": "shortlist",
        "created_at": now,
        "updated_at": now,
    }
    return NeedPipelineEntry(**{**base, **overrides})


class TestTransitions:
    @pytest.mark.parametrize(
        ("current", "target", "allowed"),
        [
            ("shortlist", "interview", False),  # собеседование — только после принятия
            ("shortlist", "offer", False),
            ("shortlist", "hired", False),
            ("invited", "interview", False),
            ("accepted", "interview", True),
            ("accepted", "offer", True),
            ("interview", "offer", True),
            ("offer", "hired", True),
            ("interview", "hired", True),
            ("shortlist", "declined", True),
            ("invited", "declined", True),
            ("accepted", "declined", True),
            ("declined", "shortlist", True),  # возврат в шорт-лист после отказа
            ("accepted", "shortlist", False),
            ("shortlist", "invited", False),  # invited — только через сервис приглашений
            ("shortlist", "accepted", False),
        ],
    )
    def test_can_transition(self, current: str, target: str, allowed: bool) -> None:
        assert can_transition(current, target) is allowed


class TestPipelineApi:
    def test_patch_invalid_transition_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        entry = entry_row(need.id, uuid.uuid4())
        session = FakeSession(scalars=[company, need, entry])
        with as_user(app, user), fake_db(app, session):
            response = client.patch(
                f"/api/v1/needs/{need.id}/pipeline/{entry.candidate_id}",
                json={"stage": "interview"},
            )
        assert response.status_code == 409
        assert response.json()["code"] == "pipeline_transition_invalid"
        assert entry.stage == "shortlist"

    def test_patch_decline_writes_history_and_audit(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        entry = entry_row(need.id, uuid.uuid4())
        session = FakeSession(scalars=[company, need, entry])
        with as_user(app, user), fake_db(app, session):
            response = client.patch(
                f"/api/v1/needs/{need.id}/pipeline/{entry.candidate_id}",
                json={"stage": "declined", "comment": "не вышел на связь"},
            )
        assert response.status_code == 200
        assert entry.stage == "declined"
        events = session.added_of(PipelineStageEvent)
        assert len(events) == 1
        assert (events[0].from_stage, events[0].to_stage) == ("shortlist", "declined")
        assert events[0].comment == "не вышел на связь"
        assert session.added_of(AuditLog)

    def test_post_adds_to_shortlist(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        # компания → потребность → кандидат → существующая строка (нет)
        session = FakeSession(scalars=[company, need, profile, None])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/needs/{need.id}/pipeline",
                json={"candidate_id": str(profile.id), "note": "из выдачи"},
            )
        assert response.status_code == 201
        body = response.json()
        assert body["stage"] == "shortlist"
        assert body["contacts_available"] is False
        entries = session.added_of(NeedPipelineEntry)
        assert len(entries) == 1 and entries[0].note == "из выдачи"

    def test_post_duplicate_409(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        profile = candidate_profile_row()
        existing = entry_row(need.id, profile.id, stage="invited")
        session = FakeSession(scalars=[company, need, profile, existing])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/needs/{need.id}/pipeline", json={"candidate_id": str(profile.id)}
            )
        assert response.status_code == 409
        assert response.json()["code"] == "pipeline_entry_exists"

    def test_get_pipeline_masks_until_accept(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["employer"])
        company = company_row(user)
        need = need_row(company.id)
        invited_profile = candidate_profile_row(pseudonym="B-1111", full_name="Иванов Иван")
        accepted_profile = candidate_profile_row(pseudonym="C-2222", full_name="Петров Пётр")
        inv1 = uuid.uuid4()
        inv2 = uuid.uuid4()
        now = datetime.now(UTC)
        invitations = [
            Invitation(
                id=inv1,
                company_id=company.id,
                candidate_id=invited_profile.id,
                title="t",
                salary_from=1,
                salary_to=2,
                status="sent",
                sent_at=now,
                expires_at=now,
                explanation={},
                created_at=now,
            ),
            Invitation(
                id=inv2,
                company_id=company.id,
                candidate_id=accepted_profile.id,
                title="t",
                salary_from=1,
                salary_to=2,
                status="accepted",
                sent_at=now,
                expires_at=now,
                explanation={},
                created_at=now,
            ),
        ]
        entries = [
            entry_row(need.id, invited_profile.id, stage="invited", invitation_id=inv1),
            entry_row(need.id, accepted_profile.id, stage="accepted", invitation_id=inv2),
        ]
        # компания → потребность → строки воронки → версия → профили → приглашения
        # → уровни целостности (нет завершённых сессий)
        session = FakeSession(
            scalars=[
                company,
                need,
                entries,
                None,
                [invited_profile, accepted_profile],
                invitations,
                [],
            ]
        )
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/needs/{need.id}/pipeline")
        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        columns = {c["stage"]: c["items"] for c in body["columns"]}
        invited_item = columns["invited"][0]
        accepted_item = columns["accepted"][0]
        # до принятия — только псевдоним, никаких ПДн
        assert invited_item["pseudonym"] == "B-1111"
        assert invited_item["contacts_available"] is False
        assert invited_item["full_name_masked"] is None
        # после принятия — маскированное имя, контакты через /invitations/{id}/contacts
        assert accepted_item["contacts_available"] is True
        assert accepted_item["full_name_masked"] == "П***в П."
        assert accepted_item["invitation_status"] == "accepted"
        assert "Иванов" not in str(body) and "Петров" not in str(body)
