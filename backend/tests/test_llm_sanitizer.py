"""Санитайзер ПДн перед LLM: регулярки → плейсхолдеры, без ложных срабатываний на коде и числах."""

from __future__ import annotations

import time

import pytest

from app.modules.llm.journal import LlmJournal
from app.modules.llm.providers.stub import StubProvider
from app.modules.llm.sanitizer import (
    MAX_TEXT_LENGTH,
    PLACEHOLDER_EMAIL,
    PLACEHOLDER_FIO,
    PLACEHOLDER_PHONE,
    PLACEHOLDER_TELEGRAM,
    PLACEHOLDER_URL,
    SanitizeInputTooLong,
    contains_pii,
    sanitize_for_llm,
    sanitize_variables,
    sanitize_with_report,
)
from app.modules.llm.service import LLMService
from tests.test_llm_helpers import NEED_VARS, reset_llm_state


@pytest.mark.parametrize(
    "text",
    [
        "пишите на ivan.petrov@example.com",
        "Контакт: Ivan_Petrov+hr@mail.example.org, отвечу",
        "почта: петров@пример.рф",
    ],
)
def test_email_replaced(text: str) -> None:
    cleaned = sanitize_for_llm(text)
    assert "@" not in cleaned and PLACEHOLDER_EMAIL in cleaned


@pytest.mark.parametrize(
    "phone",
    [
        "+7 916 123-45-67",
        "8 (916) 123 45 67",
        "8-916-123-45-67",
        "89161234567",
        "+79161234567",
        "+1-202-555-0143",
        "+7 (4712) 12-34-56",
    ],
)
def test_phone_replaced(phone: str) -> None:
    cleaned = sanitize_for_llm(f"звоните {phone} после 18:00")
    assert PLACEHOLDER_PHONE in cleaned and phone not in cleaned
    assert "18:00" in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "таблица на 1 000 000 строк, p95 ≤ 500 мс",
        "unix-время 1700000000 и число 1234567890",
        "дата 2024-10-06 12:30:45 и 06.10.2026 14:30",
        "адрес 127.0.0.1:8000, версия 3.12.7",
        "SELECT * FROM t WHERE id IN (100, 200, 300) LIMIT 10000",
    ],
)
def test_numbers_and_dates_untouched(text: str) -> None:
    assert sanitize_for_llm(text) == text
    assert not contains_pii(text)


@pytest.mark.parametrize(
    "text",
    [
        "Исполнитель: Иванов Иван Иванович, подпись",
        "Мария Петровна Сидорова вела проект",
        "ответственный Пётр Сергеевич",
        "см. отчёт Петров П.С. за март",
        "И.И. Иванов утвердил",
        "Author: Ivanov Ivan Ivanovich",
        "Козлова-Смирнова Анна Олеговна",
    ],
)
def test_full_names_replaced(text: str) -> None:
    cleaned = sanitize_for_llm(text)
    assert PLACEHOLDER_FIO in cleaned
    assert "Иванов" not in cleaned and "Ivanov" not in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "Проектирование API и Слой Данных",
        "Москва Сити, башня Федерация",
        "Составной Индекс Под Сортировку",
    ],
)
def test_capitalized_words_without_patronymic_kept(text: str) -> None:
    assert sanitize_for_llm(text) == text


def test_telegram_nick_and_link_replaced() -> None:
    cleaned = sanitize_for_llm("тг @ivan_petrov. или t.me/ivanpetrov, https://t.me/ivan_p")
    assert cleaned.count(PLACEHOLDER_TELEGRAM) == 3
    assert "ivan" not in cleaned


@pytest.mark.parametrize(
    "code",
    [
        "@router.get('/me')\nasync def me(): ...",
        "@dataclass\nclass Point: ...",
        "@pytest.fixture()\ndef client(): ...",
        "@Override\npublic void run() {}",
        "@media (max-width: 600px) { .a { color: red } }",
        "@app.post('/x')",
    ],
)
def test_decorators_untouched(code: str) -> None:
    assert sanitize_for_llm(code) == code


def test_urls_with_credentials_or_tokens_replaced() -> None:
    text = (
        "репо https://user:secret@git.example.com/x.git и "
        "https://api.example.com/v1?x=1&token=abc123 и обычная https://example.com/docs"
    )
    cleaned = sanitize_for_llm(text)
    assert cleaned.count(PLACEHOLDER_URL) == 2
    assert "secret" not in cleaned and "abc123" not in cleaned
    assert "https://example.com/docs" in cleaned


def test_snils_replaced_and_terms_hidden() -> None:
    text = "СНИЛС 123-456-789 01, работал в компании Рога и Копыта, ООО «Ромашка»"
    cleaned = sanitize_for_llm(text, terms={"Рога и Копыта": "[company]", "Ромашка": "[company]"})
    assert "[doc]" in cleaned and cleaned.count("[company]") == 2
    assert "Рога" not in cleaned and "Ромашка" not in cleaned


def test_report_counts_and_variables_recursive() -> None:
    report = sanitize_with_report("mail a@b.io, тел +7 916 123-45-67, @nick_name")
    assert report.changed
    assert report.replacements[PLACEHOLDER_EMAIL] == 1
    assert report.replacements[PLACEHOLDER_PHONE] == 1
    assert report.replacements[PLACEHOLDER_TELEGRAM] == 1

    variables = {
        "need_text": "звоните +7 916 123-45-67",
        "catalog": [{"id": "be.B1", "note": "a@b.io"}],
        "count": 3,
        "nested": {"deep": ("Иванов Иван Иванович",)},
    }
    cleaned = sanitize_variables(variables)
    assert cleaned["need_text"] == f"звоните {PLACEHOLDER_PHONE}"
    assert cleaned["catalog"][0]["note"] == PLACEHOLDER_EMAIL
    assert cleaned["count"] == 3
    assert cleaned["nested"]["deep"] == [PLACEHOLDER_FIO]
    assert not contains_pii(str(cleaned))


def test_contains_pii_detects() -> None:
    assert contains_pii("ivan@example.com")
    assert contains_pii("+7 916 123-45-67")
    assert contains_pii("Иванов Иван Иванович")
    assert not contains_pii("")
    assert not contains_pii("@dataclass")


def test_empty_and_idempotent() -> None:
    assert sanitize_for_llm("") == ""
    once = sanitize_for_llm("Иванов Иван Иванович, ivan@example.com, +7 916 123-45-67")
    assert sanitize_for_llm(once) == once


# --- ФИО заглавными, ссылки на профили, имя без отчества через terms --------------------------


@pytest.mark.parametrize(
    "text",
    ["ИВАНОВ ИВАН ИВАНОВИЧ", "резюме: ПЕТРОВА АННА СЕРГЕЕВНА, 30 лет", "ИВАН ИВАНОВИЧ сказал"],
)
def test_uppercase_fio_replaced(text: str) -> None:
    cleaned = sanitize_for_llm(text)
    assert PLACEHOLDER_FIO in cleaned and "ИВАН" not in cleaned and "АННА" not in cleaned


@pytest.mark.parametrize("text", ["ФСП ЛЦТ 2026", "SELECT COUNT(*) FROM T", "ООО РОМАШКА"])
def test_uppercase_abbreviations_kept(text: str) -> None:
    assert sanitize_for_llm(text) == text


def test_social_profile_links_replaced() -> None:
    text = (
        "github.com/ivanpetrov, https://vk.com/id12345, www.linkedin.com/in/ivan-petrov, "
        "habr.com/ru/users/ivan_p/, hh.ru/resume/abc123 — см. docs.python.org/3/library"
    )
    cleaned = sanitize_for_llm(text)
    assert cleaned.count(PLACEHOLDER_URL) == 5, cleaned
    assert "ivan" not in cleaned and "id12345" not in cleaned
    assert "docs.python.org/3/library" in cleaned


def test_first_name_without_patronymic_hidden_via_terms() -> None:
    """Регулярки ловят ФИО только с отчеством/инициалами; имя из профиля передаётся
    через terms — так resume_parse и scenario_personal не отправляют ФИО в LLM."""
    text = "Меня зовут Иван Петров, пишу на Python"
    assert sanitize_for_llm(text) == text  # без terms — норма, имя не распознаётся
    terms = {"Иван Петров": PLACEHOLDER_FIO, "Иван": PLACEHOLDER_FIO, "Петров": PLACEHOLDER_FIO}
    cleaned = sanitize_for_llm(text, terms)
    assert "Иван" not in cleaned and "Петров" not in cleaned and "Python" in cleaned


@pytest.mark.parametrize(
    "text",
    [
        "a" * 50_000,
        "a" * 24_000 + "@",
        "a://" * 8_000,
        "x@" * 10_000,
        "+7 " * 10_000,
    ],
)
def test_long_inputs_are_processed_in_linear_time(text: str) -> None:
    """Санитайзер синхронный и вызывается из async-хэндлеров: квадратичные регулярки
    подвесили бы event loop на секунды."""
    started = time.perf_counter()
    sanitize_for_llm(text)
    assert time.perf_counter() - started < 0.5


def test_input_length_limit() -> None:
    assert sanitize_for_llm("x" * MAX_TEXT_LENGTH) == "x" * MAX_TEXT_LENGTH
    with pytest.raises(SanitizeInputTooLong):
        sanitize_with_report("x" * (MAX_TEXT_LENGTH + 1))


async def test_no_pii_in_llm_calls() -> None:
    """PLAN.md §6: в журнал и к провайдеру уходят переменные без ПДн — регулярки плюс
    известные вызывающему коду имена (terms)."""
    reset_llm_state()
    journal = LlmJournal()
    service = LLMService(provider=StubProvider(), journal=journal)
    variables = {
        **NEED_VARS,
        "need_text": (
            "Контакт: Иван Петров, ИВАНОВА АННА СЕРГЕЕВНА, +7 916 123-45-67, ivan@example.com, "
            "github.com/ivanpetrov, @ivan_dev. Ищем middle backend на Python."
        ),
    }
    await service.complete(
        "need_parse", variables, session=None, terms={"Иван Петров": PLACEHOLDER_FIO}
    )
    recorded = journal.recent[-1].request_json["variables"]["need_text"]
    for secret in ("Иван", "Петров", "АННА", "916", "ivan@", "ivanpetrov", "ivan_dev"):
        assert secret not in recorded, recorded
    assert "Python" in recorded and not contains_pii(str(journal.recent[-1].request_json))
