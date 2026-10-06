"""Модуль assessment: роутеры сигналов целостности —
`POST /assessment/sessions/{id}/events` (батч телеметрии, ПДн-минимизация, 413)
и `GET /assessment/sessions/{id}/integrity` (владелец или admin, аудит просмотра)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

# модели под алиасами: имена Test* pytest пытается собрать как тест-классы
from app.models.assessment import TestSession as SessionModel
from app.models.assessment import TestSessionEvent as SessionEvent
from app.models.assessment import TestSessionIntegrity as SessionIntegrity
from app.models.audit import AuditLog
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession
from tests.test_employers_company import fake_db
from tests.test_matching_rank import candidate_profile_row

T0 = datetime(2026, 10, 8, 10, 0, 0, tzinfo=UTC)


def session_row(candidate_id: uuid.UUID, **overrides) -> SessionModel:
    base = {
        "id": uuid.uuid4(),
        "candidate_id": candidate_id,
        "specialization_code": "be",
        "target_grade": "middle",
        "mode": "full",
        "kind": "initial",
        "status": "in_progress",
        "started_at": T0,
        "created_at": T0,
    }
    return SessionModel(**{**base, **overrides})


def event_row(session_id: uuid.UUID, kind: str, offset_s: float = 0, **payload) -> SessionEvent:
    return SessionEvent(
        id=uuid.uuid4(),
        session_id=session_id,
        kind=kind,
        client_ts=T0 + timedelta(seconds=offset_s),
        payload=payload,
    )


def events_json(*events: dict) -> dict:
    return {"events": list(events)}


def paste_json(len_: int, offset_s: float = 0) -> dict:
    return {
        "kind": "paste",
        "client_ts": (T0 + timedelta(seconds=offset_s)).isoformat(),
        "payload": {"item_id": "i1", "len": len_},
    }


class TestPostEvents:
    def test_batch_accepted_and_integrity_cached(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(profile.id)
        # сессия → профиль → события сессии (пусто до батча) → строка кэша (нет)
        session = FakeSession(scalars=[ts, profile, [], None])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/assessment/sessions/{ts.id}/events",
                json=events_json(paste_json(500), paste_json(30, offset_s=10)),
            )
        assert response.status_code == 202
        assert response.json() == {"accepted": 2}
        events = session.added_of(SessionEvent)
        assert len(events) == 2
        rows = session.added_of(SessionIntegrity)
        assert len(rows) == 1
        assert rows[0].events_total == 2
        assert rows[0].level == "medium"  # одна крупная вставка
        assert session.commits == 1

    def test_answer_texts_never_stored(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(profile.id)
        session = FakeSession(scalars=[ts, profile, [], None])
        event = {
            "kind": "answer_submitted",
            "client_ts": T0.isoformat(),
            "payload": {
                "item_id": "i1",
                "len": 250,
                "chars_typed": 240,
                "text": "SELECT * FROM users",  # лишние ключи отбрасываются
                "answer": "мой ответ целиком",
            },
        }
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/assessment/sessions/{ts.id}/events", json=events_json(event)
            )
        assert response.status_code == 202
        stored = session.added_of(SessionEvent)[0]
        assert stored.payload == {"item_id": "i1", "len": 250, "chars_typed": 240}
        assert "SELECT" not in str(stored.payload)

    def test_foreign_session_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(uuid.uuid4())  # чужая сессия
        session = FakeSession(scalars=[ts, profile])
        with as_user(app, user), fake_db(app, session):
            response = client.post(
                f"/api/v1/assessment/sessions/{ts.id}/events", json=events_json(paste_json(10))
            )
        assert response.status_code == 404
        assert session.added_of(SessionEvent) == []

    def test_employer_forbidden(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["employer"])), fake_db(app, FakeSession()):
            response = client.post(
                f"/api/v1/assessment/sessions/{uuid.uuid4()}/events",
                json=events_json(paste_json(10)),
            )
        assert response.status_code == 403

    def test_oversized_batch_413(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        batch = events_json(*(paste_json(10, offset_s=i) for i in range(501)))
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(f"/api/v1/assessment/sessions/{uuid.uuid4()}/events", json=batch)
        assert response.status_code == 413
        assert response.json()["code"] == "too_many_events"

    def test_unknown_kind_400(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        event = {"kind": "keylog", "client_ts": T0.isoformat(), "payload": {}}
        with as_user(app, user), fake_db(app, FakeSession()):
            response = client.post(
                f"/api/v1/assessment/sessions/{uuid.uuid4()}/events", json=events_json(event)
            )
        assert response.status_code == 400
        assert response.json()["code"] == "validation_error"


class TestGetIntegrity:
    def test_owner_gets_levels_and_cache_written(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(profile.id, status="finished", finished_at=T0 + timedelta(hours=1))
        events = [
            event_row(ts.id, "paste", 1, len=400),
            event_row(ts.id, "blur", 5),
            event_row(ts.id, "focus", 30),
        ]
        # сессия → профиль → события → строка кэша (нет)
        session = FakeSession(scalars=[ts, profile, events, None])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/assessment/sessions/{ts.id}/integrity")
        assert response.status_code == 200
        body = response.json()
        assert body["level"] == "medium"
        assert body["events_total"] == 3
        assert [f["code"] for f in body["flags"]] == ["large_paste", "tab_blur"]
        assert all(f["title"] and f["detail"] for f in body["flags"])
        assert len(session.added_of(SessionIntegrity)) == 1
        assert session.added_of(AuditLog) == []  # свой просмотр не аудируется

    def test_admin_view_writes_audit(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["admin"])
        ts = session_row(uuid.uuid4())
        events = [event_row(ts.id, "paste", 1, len=400)]
        # сессия → события → строка кэша (профиль не запрашивается — роли candidate нет)
        session = FakeSession(scalars=[ts, events, None])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/assessment/sessions/{ts.id}/integrity")
        assert response.status_code == 200
        audits = session.added_of(AuditLog)
        assert len(audits) == 1
        assert audits[0].action == "integrity_viewed"
        assert audits[0].entity_id == ts.id

    def test_foreign_candidate_404(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(uuid.uuid4())
        session = FakeSession(scalars=[ts, profile])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/assessment/sessions/{ts.id}/integrity")
        assert response.status_code == 404

    def test_recompute_updates_existing_cache_row(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(profile.id)
        existing = SessionIntegrity(
            session_id=ts.id,
            candidate_id=profile.id,
            level="high",
            flags=[],
            events_total=1,
            computed_at=T0,
        )
        events = [event_row(ts.id, "paste", 1, len=400)]
        session = FakeSession(scalars=[ts, profile, events, existing])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/assessment/sessions/{ts.id}/integrity")
        assert response.status_code == 200
        assert response.json()["level"] == "medium"
        assert session.added_of(SessionIntegrity) == []  # upsert, не вторая строка
        assert existing.level == "medium"
        assert existing.events_total == 1

    def test_no_events_high_without_cache_row(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = candidate_profile_row(user_id=user.id)
        ts = session_row(profile.id)
        # сессия → профиль → события (нет); кэш не запрашивается и не создаётся
        session = FakeSession(scalars=[ts, profile, []])
        with as_user(app, user), fake_db(app, session):
            response = client.get(f"/api/v1/assessment/sessions/{ts.id}/integrity")
        assert response.status_code == 200
        body = response.json()
        assert body["level"] == "high"
        assert body["events_total"] == 0
        assert session.added_of(SessionIntegrity) == []
