"""Модуль candidates (минимум дня 6): `GET|PUT /candidates/me` — создание профиля с
псевдонимом при первом обращении, шифрование ПДн + blind index, согласие, роли, валидаторы.

PostgreSQL нет: сессия подменяется `FakeSession`, шифрование проверяется на уровне
типа `EncryptedStr` (bind/result) и blind-index-колонок, которые заполняет сервис.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.db import get_session
from app.core.pii import EncryptedStr, get_pii_crypto
from app.models.candidate import DEFAULT_PRIVACY, CandidateCategory, CandidateProfile
from app.models.skills import Industry
from app.modules.candidates import service
from app.modules.candidates.schemas import CandidateMeIn, normalize_tags
from tests.conftest import as_user, make_user
from tests.fake_session import FakeSession, integrity_error

PSEUDONYM_RE = re.compile(r"^[A-HJ-NP-Z]-[0-9A-F]{4}$")


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


def _profile(user, **overrides) -> CandidateProfile:
    """Существующий профиль «из БД»: id и timestamps уже проставлены."""
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "user_id": user.id,
        "pseudonym": "B-7F3A",
        "privacy": dict(DEFAULT_PRIVACY),
        "searchable": False,
        "created_at": now,
        "updated_at": now,
    }
    return CandidateProfile(**{**base, **overrides})


class TestAccess:
    def test_requires_candidate_role(self, app: FastAPI, client: TestClient) -> None:
        with as_user(app, make_user(roles=["employer"])), fake_db(app, FakeSession()):
            response = client.get("/api/v1/candidates/me")
        assert response.status_code == 403 and response.json()["code"] == "forbidden"

    def test_anonymous_401(self, client: TestClient) -> None:
        assert client.get("/api/v1/candidates/me").status_code == 401

    def test_put_requires_consent(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=False)
        with as_user(app, user), fake_db(app, FakeSession()) as session:
            response = client.put("/api/v1/candidates/me", json={"city": "Казань"})
        assert response.status_code == 403
        body = response.json()
        assert body["code"] == "consent_required"  # фронт ведёт на экран согласия, не на 403
        assert "согласие" in body["message"].lower()
        assert session.commits == 0 and session.added == []


class TestGetMe:
    def test_creates_profile_on_first_access(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=False)
        session = FakeSession(scalars=[None])  # профиля ещё нет
        with as_user(app, user), fake_db(app, session):
            response = client.get("/api/v1/candidates/me")
        assert response.status_code == 200, response.text
        body = response.json()
        assert PSEUDONYM_RE.match(body["pseudonym"]), body["pseudonym"]
        assert body["full_name"] is None and body["phone"] is None and body["telegram"] is None
        assert body["stack_tags_declared"] == [] and body["searchable"] is False
        assert body["privacy"] == {**DEFAULT_PRIVACY}
        created = session.added_of(CandidateProfile)
        assert len(created) == 1 and created[0].user_id == user.id
        assert session.commits == 1

    def test_returns_existing_profile(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"])
        profile = _profile(user, city="Москва", full_name="Иванова Анна")
        session = FakeSession(scalars=[profile])
        with as_user(app, user), fake_db(app, session):
            body = client.get("/api/v1/candidates/me").json()
        assert body["pseudonym"] == "B-7F3A" and body["city"] == "Москва"
        assert body["full_name"] == "Иванова Анна"  # свои данные — открыто
        assert session.added == [] and session.commits == 0

    async def test_pseudonym_collision_retries(self) -> None:
        user = make_user(roles=["candidate"])
        session = FakeSession(
            scalars=[None],
            flush_errors=[integrity_error("uq_candidate_profile_pseudonym"), None],
        )
        profile = await service.get_or_create_profile(session, user)
        assert PSEUDONYM_RE.match(profile.pseudonym)
        assert session.commits == 1 and len(session.added_of(CandidateProfile)) == 1

    async def test_user_id_race_returns_existing(self) -> None:
        user = make_user(roles=["candidate"])
        existing = _profile(user)
        session = FakeSession(
            scalars=[None, existing], flush_errors=[integrity_error("uq_candidate_profile_user_id")]
        )
        profile = await service.get_or_create_profile(session, user)
        assert profile is existing and session.commits == 0


class TestPutMe:
    def test_updates_and_encrypts(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user)
        session = FakeSession(scalars=[profile], rows={(Industry, "fintech"): Industry()})
        payload = {
            "full_name": "  Иванова   Анна ",
            "phone": "8 (916) 000-11-22",
            "telegram": "@anna_dev",
            "city": "Москва",
            "industry_code": "fintech",
            "stack_tags_declared": ["Python", "python", " FastAPI ", ""],
            "experience_years": 3,
            "roles_declared": ["Backend"],
            "about": "Люблю\x00 бэкенд",
            "salary_floor": 150000,
        }
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/candidates/me", json=payload)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["full_name"] == "Иванова   Анна"
        assert body["phone"] == "+79160001122" and body["telegram"] == "anna_dev"
        assert body["stack_tags_declared"] == ["python", "fastapi"]
        assert body["roles_declared"] == ["backend"] and body["about"] == "Люблю бэкенд"
        assert body["salary_floor"] == 150000 and body["industry_code"] == "fintech"
        crypto = get_pii_crypto()
        assert profile.full_name_hash == crypto.full_name_hmac("Иванова   Анна")
        assert profile.phone_hash == crypto.phone_hmac("+79160001122")
        assert profile.telegram_hash == crypto.blind_index("telegram", "anna_dev")
        assert profile.about_sanitized == "Люблю бэкенд"
        assert session.commits == 1

    def test_partial_update_keeps_other_fields(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, city="Казань", phone="+79160000000", phone_hash="x" * 64)
        session = FakeSession(scalars=[profile])
        with as_user(app, user), fake_db(app, session):
            body = client.put("/api/v1/candidates/me", json={"city": "Самара"}).json()
        assert body["city"] == "Самара" and body["phone"] == "+79160000000"
        assert profile.phone_hash == "x" * 64

    def test_clearing_pii_clears_hash(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, phone="+79160000000", phone_hash="x" * 64)
        session = FakeSession(scalars=[profile])
        with as_user(app, user), fake_db(app, session):
            body = client.put("/api/v1/candidates/me", json={"phone": None}).json()
        assert body["phone"] is None and profile.phone_hash is None

    @pytest.mark.parametrize(
        "field", ["stack_tags_declared", "roles_declared", "soft_skills_declared"]
    )
    def test_null_list_clears_field(self, app: FastAPI, client: TestClient, field: str) -> None:
        """Колонки списков NOT NULL: явный null — «очистить», а не 500 от NotNullViolation."""
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, **{field: ["python"]})
        session = FakeSession(scalars=[profile])
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/candidates/me", json={field: None})
        assert response.status_code == 200, response.text
        assert response.json()[field] == [] and getattr(profile, field) == []
        assert session.commits == 1

    def test_about_is_sanitized_before_storage(self, app: FastAPI, client: TestClient) -> None:
        """`about_sanitized` оправдывает имя: контакты, ссылки на профили и ФИО (включая имя
        без отчества, известное из профиля) заменяются плейсхолдерами."""
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, full_name="Петров Иван", full_name_hash="f" * 64)
        session = FakeSession(scalars=[profile])
        about = (
            "Я Иван Петров, звоните +7 916 123-45-67, пишите ivan@mail.ru или @ivan_dev, "
            "код на github.com/ivanpetrov. Люблю бэкенд."
        )
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/candidates/me", json={"about": about})
        assert response.status_code == 200, response.text
        stored = profile.about_sanitized
        assert stored == response.json()["about"]
        for secret in ("Иван", "Петров", "916", "ivan@mail.ru", "ivan_dev", "ivanpetrov"):
            assert secret not in stored, stored
        for placeholder in ("[fio]", "[phone]", "[email]", "[telegram]", "[url]"):
            assert placeholder in stored, stored
        assert "Люблю бэкенд" in stored
        assert service.sanitize_about(profile, "   ") is None

    def test_privacy_saved_and_searchable_with_category(
        self, app: FastAPI, client: TestClient
    ) -> None:
        """privacy.searchable сохраняется и включает видимость в банке, когда категория
        уже присвоена (инвариант §3.7: privacy.searchable И категория есть)."""
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, privacy={**DEFAULT_PRIVACY, "searchable": False})
        category = CandidateCategory(
            id=uuid.uuid4(), candidate_id=profile.id, specialization_code="be", grade="junior"
        )
        session = FakeSession(scalars=[profile, category])
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/candidates/me", json={"privacy": {"searchable": True}})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["privacy"]["searchable"] is True
        assert body["searchable"] is True
        assert profile.privacy["searchable"] is True
        # непереданные ключи privacy не изменились
        assert profile.privacy["show_city"] is True
        assert profile.searchable is True
        assert session.commits == 1

    def test_privacy_searchable_off_hides_profile(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user, searchable=True)
        category = CandidateCategory(
            id=uuid.uuid4(), candidate_id=profile.id, specialization_code="be", grade="junior"
        )
        session = FakeSession(scalars=[profile, category])
        with as_user(app, user), fake_db(app, session):
            body = client.put(
                "/api/v1/candidates/me", json={"privacy": {"searchable": False}}
            ).json()
        assert body["searchable"] is False and profile.searchable is False
        assert profile.privacy["searchable"] is False

    def test_privacy_without_category_keeps_hidden(self, app: FastAPI, client: TestClient) -> None:
        """Без категории searchable=True в privacy сохраняется, но профиль в банке
        не виден (эффективный флаг остаётся False до прохождения теста)."""
        user = make_user(roles=["candidate"], consent=True)
        profile = _profile(user)
        session = FakeSession(scalars=[profile, None])  # категории ещё нет
        with as_user(app, user), fake_db(app, session):
            body = client.put(
                "/api/v1/candidates/me", json={"privacy": {"searchable": True}}
            ).json()
        assert body["privacy"]["searchable"] is True
        assert body["searchable"] is False and profile.searchable is False

    def test_unknown_industry_400(self, app: FastAPI, client: TestClient) -> None:
        user = make_user(roles=["candidate"], consent=True)
        session = FakeSession(scalars=[_profile(user)])
        with as_user(app, user), fake_db(app, session):
            response = client.put("/api/v1/candidates/me", json={"industry_code": "nope"})
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["details"] == [
            {"field": "industry_code", "code": "unknown", "message": "Нет такой отрасли"}
        ]
        assert session.commits == 0

    @pytest.mark.parametrize(
        ("payload", "field"),
        [
            ({"salary_floor": 0}, "salary_floor"),
            ({"salary_floor": -5}, "salary_floor"),
            ({"experience_years": 70}, "experience_years"),
            ({"phone": "123"}, "phone"),
            ({"telegram": "ab"}, "telegram"),
            ({"about": "x" * 2001}, "about"),
        ],
    )
    def test_validation_errors(
        self, app: FastAPI, client: TestClient, payload: dict, field: str
    ) -> None:
        user = make_user(roles=["candidate"], consent=True)
        with as_user(app, user), fake_db(app, FakeSession(scalars=[_profile(user)])):
            response = client.put("/api/v1/candidates/me", json=payload)
        assert response.status_code == 400, response.text
        body = response.json()
        assert body["code"] == "validation_error"
        assert body["details"][0]["field"] == field


class TestSchemasAndCrypto:
    def test_changes_only_set_fields(self) -> None:
        data = CandidateMeIn.model_validate({"city": "Тула", "phone": None})
        assert data.changes() == {"city": "Тула", "phone": None}
        assert CandidateMeIn().changes() == {}

    def test_normalize_tags(self) -> None:
        assert normalize_tags([" Go ", "go", "Rust", "", "  "]) == ["go", "rust"]
        assert normalize_tags([f"t{i}" for i in range(100)]) == [f"t{i}" for i in range(50)]
        assert normalize_tags(None) == []

    def test_phone_and_telegram_validators(self) -> None:
        data = CandidateMeIn(phone="+7 (916) 000-11-22", telegram="@Anna_Dev")
        assert data.phone == "+79160001122" and data.telegram == "Anna_Dev"
        with pytest.raises(ValidationError):
            CandidateMeIn(telegram="bad name!")

    def test_encrypted_str_roundtrip_and_ciphertext(self) -> None:
        column_type = EncryptedStr()
        raw = "Иванова Анна"
        stored = column_type.process_bind_param(raw, None)
        assert isinstance(stored, bytes) and raw.encode("utf-8") not in stored
        assert column_type.process_result_value(stored, None) == raw
        assert column_type.process_bind_param(None, None) is None

    def test_apply_pii_sets_hash_and_clears(self) -> None:
        profile = CandidateProfile(pseudonym="B-0000")
        service.apply_pii(profile, "full_name", "Петров Пётр")
        assert profile.full_name == "Петров Пётр"
        assert profile.full_name_hash == get_pii_crypto().full_name_hmac("Петров Петр")
        service.apply_pii(profile, "full_name", None)
        assert profile.full_name is None and profile.full_name_hash is None
