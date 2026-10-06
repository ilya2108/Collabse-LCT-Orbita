"""Модуль tgbot: чистые сборщики текста дайджеста (без БД и aiogram).

Проверяются тексты кандидата и работодателя: содержимое, лимит топ-5, пустые состояния,
отсутствие ПДн по построению (кандидаты — только псевдонимами).
"""

from __future__ import annotations

import uuid

from app.modules.tgbot.digest import (
    CandidateDigest,
    DigestCandidate,
    DigestInvitation,
    DigestNeed,
    DigestVacancy,
    EmployerDigest,
    build_candidate_digest,
    build_employer_digest,
    format_salary,
    invitation_buttons,
    invitation_push_text,
)
from app.modules.tgbot.service import pack_uuid, unpack_uuid


def vacancy(index: int, score: float = 0.8) -> DigestVacancy:
    return DigestVacancy(
        need_id=uuid.uuid4(),
        title=f"Backend-разработчик {index}",
        company_name="ФСП.Орбита Демо",
        grade="middle",
        salary_from=180_000,
        salary_to=260_000,
        score=score,
        why=f"Закрывает {index} из 7 узлов потребности",
    )


class TestFormatSalary:
    def test_range(self) -> None:
        assert format_salary(80_000, 130_000) == "80–130 тыс. ₽"

    def test_missing(self) -> None:
        assert format_salary(None, None) == "з/п не указана"


class TestCandidateDigest:
    def test_contains_vacancies_and_invitations(self) -> None:
        data = CandidateDigest(
            pseudonym="B-7F3A",
            grade="middle",
            specialization="be",
            vacancies=[vacancy(1)],
            invitations=[
                DigestInvitation(
                    invitation_id=uuid.uuid4(),
                    company_name="ФСП.Орбита Демо",
                    title="Backend middle",
                    status="sent",
                    salary_from=180_000,
                    salary_to=260_000,
                )
            ],
        )
        text = build_candidate_digest(data)
        assert "ФСП.Орбита" in text
        assert "B-7F3A" in text and "мидл" in text
        assert "«Backend-разработчик 1» — ФСП.Орбита Демо · 180–260 тыс. ₽ · match 80%" in text
        assert "Почему вы: Закрывает 1 из 7 узлов потребности" in text
        assert "«Backend middle» от ФСП.Орбита Демо · 180–260 тыс. ₽ · ожидает ответа" in text

    def test_top_five_limit(self) -> None:
        data = CandidateDigest(
            pseudonym="B-7F3A",
            grade=None,
            specialization=None,
            vacancies=[vacancy(i) for i in range(1, 8)],
        )
        text = build_candidate_digest(data)
        assert "Backend-разработчик 5" in text
        assert "Backend-разработчик 6" not in text

    def test_empty_state(self) -> None:
        text = build_candidate_digest(
            CandidateDigest(pseudonym="B-7F3A", grade=None, specialization=None)
        )
        assert "Новых подходящих вакансий пока нет" in text
        assert "Ваши приглашения" not in text


class TestEmployerDigest:
    def test_candidates_are_pseudonyms_only(self) -> None:
        data = EmployerDigest(
            company_name="ФСП.Орбита Демо",
            needs=[
                DigestNeed(
                    need_id=uuid.uuid4(),
                    title="Backend middle",
                    grade="middle",
                    candidates=[
                        DigestCandidate(
                            pseudonym="K-11AB", grade="middle", score=0.9, grade_confirmed=True
                        ),
                        DigestCandidate(
                            pseudonym="M-22CD", grade="senior", score=0.7, grade_confirmed=False
                        ),
                    ],
                    pipeline_counts={"shortlist": 2, "invited": 1},
                )
            ],
        )
        text = build_employer_digest(data)
        assert "дайджест для «ФСП.Орбита Демо»" in text
        assert "1. K-11AB · мидл · match 90%" in text
        assert "2. M-22CD · сеньор · match 70% · грейд не совпадает" in text
        assert "Воронка: шорт-лист: 2 · приглашены: 1" in text

    def test_no_needs(self) -> None:
        text = build_employer_digest(EmployerDigest(company_name="ООО Тест"))
        assert "Активных потребностей нет" in text

    def test_need_without_candidates(self) -> None:
        data = EmployerDigest(
            company_name="ООО Тест",
            needs=[DigestNeed(need_id=uuid.uuid4(), title="Backend junior", grade="junior")],
        )
        text = build_employer_digest(data)
        assert "подходящих кандидатов пока нет" in text


class TestInvitationPush:
    def test_text_and_buttons(self) -> None:
        class FakeInvitation:
            title = "Backend middle"
            salary_from = 180_000
            salary_to = 260_000
            explanation = {"text": "Приглашение по потребности «Backend middle»"}

        text = invitation_push_text("ФСП.Орбита Демо", FakeInvitation())  # type: ignore[arg-type]
        assert "новое приглашение" in text
        assert "180–260 тыс. ₽" in text
        assert "Почему вы:" in text
        invitation_id = uuid.uuid4()
        buttons = invitation_buttons(invitation_id)
        callbacks = [button["callback_data"] for row in buttons for button in row]
        assert all(len(data.encode()) <= 64 for data in callbacks)
        assert callbacks[0] == f"a:{pack_uuid(invitation_id)}"
        assert callbacks[1].startswith("d:")

    def test_pack_unpack_roundtrip(self) -> None:
        value = uuid.uuid4()
        packed = pack_uuid(value)
        assert len(packed) == 22
        assert unpack_uuid(packed) == value
