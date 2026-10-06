"""Санитайзер ПДн перед отправкой текста в LLM (PLAN.md §6 «Что не отправляем в LLM»).

`sanitize_for_llm(text)` заменяет регулярками на плейсхолдеры: ФИО («Фамилия Имя Отчество»,
«Имя Отчество Фамилия», «Фамилия И.О.», то же заглавными буквами), email, телефоны, ссылки
с логином/паролем или токеном в запросе, ссылки на профили (github, gitlab, linkedin, vk,
habr, hh), telegram-ники и ссылки t.me, СНИЛС. Имя без отчества регулярки не ловят —
названия компаний и имена, известные вызывающему коду (профиль кандидата), передаются
через `terms` и подставляются как плейсхолдеры без регулярок; для `resume_parse` и
`scenario_personal` вызывающий код обязан передать ФИО из профиля. Идентификаторы
в LLM — только UUID, здесь их не трогаем.

Регулярки с ограниченными квантификаторами и якорями: длинная строка без пробелов
(вставленный лог, base64) не должна подвешивать event loop; текст длиннее MAX_TEXT_LENGTH
отклоняется (`SanitizeInputTooLong`) — вызывающий код режет вход заранее.

Плейсхолдеры в квадратных скобках (`[email]`, `[phone]`, …) — модель видит, что данные
намеренно скрыты, и не пытается их «восстановить». Регулярки намеренно консервативны —
ответы кандидатов содержат код и числа: «1 000 000 строк», unix-время, даты
«2024-10-06 12:30», декораторы `@router.get` и `@dataclass` остаются как есть.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

PLACEHOLDER_EMAIL = "[email]"
PLACEHOLDER_PHONE = "[phone]"
PLACEHOLDER_FIO = "[fio]"
PLACEHOLDER_URL = "[url]"
PLACEHOLDER_TELEGRAM = "[telegram]"
PLACEHOLDER_DOC = "[doc]"
PLACEHOLDER_TERM = "[hidden]"

PII_PLACEHOLDERS = frozenset(
    {
        PLACEHOLDER_EMAIL,
        PLACEHOLDER_PHONE,
        PLACEHOLDER_FIO,
        PLACEHOLDER_URL,
        PLACEHOLDER_TELEGRAM,
        PLACEHOLDER_DOC,
        PLACEHOLDER_TERM,
    }
)

# --- регулярки ----------------------------------------------------------------------------------

# максимальная длина текста на один вызов санитайзера (резюме ~ 20–30 тыс. символов)
MAX_TEXT_LENGTH = 100_000
# верхняя граница длины URL/токена в регулярках — квантификаторы ограничены намеренно
_MAX_URL = 4096


class SanitizeInputTooLong(ValueError):
    """Текст длиннее MAX_TEXT_LENGTH — вызывающий код должен ограничить вход заранее."""


# lookbehind не даёт начинать совпадение внутри слова: иначе на длинной строке без «@»
# движок перебирает все стартовые позиции квадратично
_EMAIL_RE = re.compile(r"(?<![\w.+-])[\w.+-]{1,64}@[\w-]{1,63}(?:\.[\w-]{1,63}){1,8}")

# схема://логин[:пароль]@хост… — целиком, вместе с хостом (в логине может быть email)
_URL_WITH_CREDENTIALS_RE = re.compile(
    rf"\b[a-z][a-z0-9+.\-]*://[^\s/@]{{1,256}}@\S{{1,{_MAX_URL}}}", re.IGNORECASE
)
# любая ссылка; секрет в query ищется коллбэком по уже найденному URL (линейно)
_URL_RE = re.compile(rf"\b[a-z][a-z0-9+.\-]*://\S{{1,{_MAX_URL}}}", re.IGNORECASE)
_SECRET_PARAM_RE = re.compile(
    r"[?&#](?:token|access_token|api_key|apikey|key|secret|password|pass|pwd|auth|sig|signature)=",
    re.IGNORECASE,
)
# ссылки на профили людей: github/gitlab/linkedin/vk/habr/hh/соцсети — идентифицируют человека
_SOCIAL_LINK_RE = re.compile(
    r"(?<![\w/])(?:https?://)?(?:www\.)?"
    r"(?:github\.com|gitlab\.com|linkedin\.com/in|vk\.com|habr\.com/(?:ru/)?users|"
    r"hh\.ru/resume|facebook\.com|instagram\.com|x\.com|twitter\.com)"
    r"/[A-Za-z0-9_.\-]{2,64}(?:/[A-Za-z0-9_.\-]{1,64})?/?(?![\w/])",
    re.IGNORECASE,
)
_TELEGRAM_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/[A-Za-z0-9_+/-]{3,}", re.I
)
# @ник telegram (5–32 символа, начинается с буквы). Email уже заменён, поэтому «@» внутри
# адреса сюда не попадает. Не ник: часть слова (`(?<![\w@.])`), обращение к атрибуту
# `@router.get` (`\.\w`), вызов `@app(` — и слова из списка декораторов/at-правил ниже.
_TELEGRAM_NICK_RE = re.compile(r"(?<![\w@.])@(?P<nick>[A-Za-z][A-Za-z0-9_]{4,31})(?!\w|@|\.\w|\()")
_NOT_TELEGRAM_WORDS = frozenset(
    {
        # python
        "dataclass", "property", "staticmethod", "classmethod", "abstractmethod", "override",
        "pytest", "fixture", "contextmanager", "asynccontextmanager", "lru_cache", "cache",
        "wraps", "validator", "field_validator", "model_validator", "router", "pytest_fixture",
        "overload", "final", "singledispatch", "cached_property",
        # java / kotlin / c#
        "Override", "Autowired", "Bean", "Service", "Controller", "RestController", "Entity",
        "Table", "Column", "Transactional", "Test", "BeforeEach", "AfterEach", "Mock", "Inject",
        "Component", "Injectable", "Input", "Output", "Deprecated", "Nullable", "NotNull",
        "GetMapping", "PostMapping", "RequestMapping", "Composable", "Serializable",
        # css / scss / js-декораторы
        "media", "import", "keyframes", "apply", "layer", "tailwind", "supports", "charset",
        "mixin", "include", "extend", "font", "namespace", "observable", "action", "computed",
    }
)  # fmt: skip

# телефон: код страны (+7, +1…) или «8», код города/оператора (в скобках или нет), затем
# местная часть 3-2-2 / 3-4 / 2-2-2 с разделителями; либо слитные 11 цифр с 7/8 в начале.
# Хвост `(?!\w|:)` отсекает время «… 12:30»; коллбэк дополнительно требует признак телефона
# («+», скобки, разделитель или 11 цифр с 7/8), иначе «1234567890» остаётся числом.
_PHONE_RE = re.compile(
    r"(?<![\w+])"
    r"(?:"
    r"(?:(?:\+\d{1,3}|8)[\s\-.]?)?"
    r"(?:\(\d{3,5}\)|\d{3,5})[\s\-.]?"
    r"(?:\d{3}[\s\-.]?\d{2}[\s\-.]?\d{2}|\d{3}[\s\-.]?\d{4}|\d{2}[\s\-.]?\d{2}[\s\-.]?\d{2})"
    r"|\+?[78]\d{10}"
    r")"
    r"(?!\w|:)"
)
_PHONE_MIN_DIGITS = 10
_PHONE_MAX_DIGITS = 15
_SEPARATOR_RE = re.compile(r"[\s\-.()]")

_CAP_RU = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?"
_PATRONYMIC_RU = r"[А-ЯЁ][а-яё]+(?:ович|евич|ич|овна|евна|ична|инична|улы|уулу|оглы|кызы|заде)"
# «Фамилия Имя Отчество», «Имя Отчество Фамилия», «Имя Отчество»
_FIO_RU_RE = re.compile(
    rf"(?<![\w-])(?:{_CAP_RU}\s+){{1,2}}{_PATRONYMIC_RU}(?:\s+{_CAP_RU})?(?![\w-])"
)
_CAP_LAT = r"[A-Z][a-z]+(?:-[A-Z][a-z]+)?"
_PATRONYMIC_LAT = r"[A-Z][a-z]+(?:ovich|evich|ovna|evna)"
_FIO_LAT_RE = re.compile(
    rf"(?<![\w-])(?:{_CAP_LAT}\s+){{1,2}}{_PATRONYMIC_LAT}(?:\s+{_CAP_LAT})?(?![\w-])"
)
# «Фамилия И.О.» и «И.О. Фамилия»
_FIO_INITIALS_RE = re.compile(
    rf"(?<![\w-])(?:{_CAP_RU}\s+[А-ЯЁ]\.\s?[А-ЯЁ]\.|[А-ЯЁ]\.\s?[А-ЯЁ]\.\s+{_CAP_RU})(?![\w-])"
)
# ФИО заглавными буквами: «ИВАНОВ ИВАН ИВАНОВИЧ», «ИВАН ИВАНОВИЧ» (отчество обязательно —
# аббревиатуры «ФСП ЛЦТ» остаются)
_CAP_RU_UPPER = r"[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?"
_PATRONYMIC_RU_UPPER = r"[А-ЯЁ]+(?:ОВИЧ|ЕВИЧ|ОВНА|ЕВНА|ИЧНА|ИНИЧНА|ОГЛЫ|КЫЗЫ|УЛЫ|УУЛУ)"
_FIO_RU_UPPER_RE = re.compile(
    rf"(?<![\w-])(?:{_CAP_RU_UPPER}\s+){{1,2}}{_PATRONYMIC_RU_UPPER}(?:\s+{_CAP_RU_UPPER})?(?![\w-])"
)
# СНИЛС «123-456-789 01»
_SNILS_RE = re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[ -]\d{2}(?!\d)")


def _is_phone(value: str) -> bool:
    digits = sum(ch.isdigit() for ch in value)
    if not _PHONE_MIN_DIGITS <= digits <= _PHONE_MAX_DIGITS:
        return False
    if "+" in value or "(" in value or _SEPARATOR_RE.search(value):
        return True
    return digits == 11 and value[0] in "78"


def _phone_replacement(match: re.Match[str]) -> str:
    return PLACEHOLDER_PHONE if _is_phone(match.group(0)) else match.group(0)


def _is_telegram(match: re.Match[str]) -> bool:
    return match.group("nick") not in _NOT_TELEGRAM_WORDS


def _telegram_replacement(match: re.Match[str]) -> str:
    return PLACEHOLDER_TELEGRAM if _is_telegram(match) else match.group(0)


def _url_secret_replacement(match: re.Match[str]) -> str:
    return PLACEHOLDER_URL if _SECRET_PARAM_RE.search(match.group(0)) else match.group(0)


Replacement = str | Callable[[re.Match[str]], str]

# порядок важен: ссылки с логином — до email, ссылки на профили — до общих URL,
# email — до telegram-ников, телефон — до СНИЛС
_PIPELINE: tuple[tuple[str, re.Pattern[str], Replacement], ...] = (
    (PLACEHOLDER_URL, _URL_WITH_CREDENTIALS_RE, PLACEHOLDER_URL),
    (PLACEHOLDER_URL, _SOCIAL_LINK_RE, PLACEHOLDER_URL),
    (PLACEHOLDER_URL, _URL_RE, _url_secret_replacement),
    (PLACEHOLDER_TELEGRAM, _TELEGRAM_LINK_RE, PLACEHOLDER_TELEGRAM),
    (PLACEHOLDER_EMAIL, _EMAIL_RE, PLACEHOLDER_EMAIL),
    (PLACEHOLDER_PHONE, _PHONE_RE, _phone_replacement),
    (PLACEHOLDER_FIO, _FIO_RU_RE, PLACEHOLDER_FIO),
    (PLACEHOLDER_FIO, _FIO_RU_UPPER_RE, PLACEHOLDER_FIO),
    (PLACEHOLDER_FIO, _FIO_LAT_RE, PLACEHOLDER_FIO),
    (PLACEHOLDER_FIO, _FIO_INITIALS_RE, PLACEHOLDER_FIO),
    (PLACEHOLDER_TELEGRAM, _TELEGRAM_NICK_RE, _telegram_replacement),
    (PLACEHOLDER_DOC, _SNILS_RE, PLACEHOLDER_DOC),
)


@dataclass
class SanitizeResult:
    """Очищенный текст и счётчик замен по плейсхолдерам (для тестов и аудита)."""

    text: str
    replacements: Counter[str] = field(default_factory=Counter)

    @property
    def changed(self) -> bool:
        return sum(self.replacements.values()) > 0


def sanitize_with_report(text: str, terms: Mapping[str, str] | None = None) -> SanitizeResult:
    """Полная очистка с отчётом о заменах. `terms` — известные значения → плейсхолдер
    (названия компаний-работодателей, ФИО из профиля); сравнение без учёта регистра,
    короче 3 символов игнорируются."""
    result = SanitizeResult(text=text)
    if not text:
        return result
    if len(text) > MAX_TEXT_LENGTH:
        raise SanitizeInputTooLong(
            f"текст длиннее {MAX_TEXT_LENGTH} символов не обрабатывается — сократите вход"
        )
    current = text
    for placeholder, pattern, replacement in _PIPELINE:
        if callable(replacement):
            # коллбэк сам решает, менять ли совпадение — считаем только реальные замены
            def _counted(match: re.Match[str], _p: str = placeholder, _r=replacement) -> str:
                value = _r(match)
                if value == _p:
                    result.replacements[_p] += 1
                return value

            current = pattern.sub(_counted, current)
        else:
            current, count = pattern.subn(replacement, current)
            if count:
                result.replacements[placeholder] += count
    if terms:
        for raw_term, placeholder in terms.items():
            term = (raw_term or "").strip()
            if len(term) < 3:
                continue
            term_re = re.compile(r"(?<!\w)" + re.escape(term) + r"(?!\w)", re.IGNORECASE)
            current, count = term_re.subn(placeholder or PLACEHOLDER_TERM, current)
            if count:
                result.replacements[placeholder or PLACEHOLDER_TERM] += count
    result.text = current
    return result


def sanitize_for_llm(text: str, terms: Mapping[str, str] | None = None) -> str:
    """Текст без ПДн: email, телефоны, ФИО, ссылки с секретами, telegram → плейсхолдеры."""
    return sanitize_with_report(text, terms).text


def sanitize_variables(value: Any, terms: Mapping[str, str] | None = None) -> Any:
    """Рекурсивная очистка строк внутри dict/list/tuple (переменные промпта)."""
    if isinstance(value, str):
        return sanitize_for_llm(value, terms)
    if isinstance(value, Mapping):
        return {key: sanitize_variables(item, terms) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [sanitize_variables(item, terms) for item in value]
    return value


def contains_pii(text: str) -> bool:
    """Есть ли в тексте признаки ПДн (для теста `test_no_pii_in_llm_calls` и аудита журнала)."""
    if not text:
        return False
    for _placeholder, pattern, replacement in _PIPELINE:
        for match in pattern.finditer(text):
            if not callable(replacement) or replacement(match) != match.group(0):
                return True
    return False


__all__ = [
    "MAX_TEXT_LENGTH",
    "PII_PLACEHOLDERS",
    "PLACEHOLDER_DOC",
    "PLACEHOLDER_EMAIL",
    "PLACEHOLDER_FIO",
    "PLACEHOLDER_PHONE",
    "PLACEHOLDER_TELEGRAM",
    "PLACEHOLDER_TERM",
    "PLACEHOLDER_URL",
    "SanitizeInputTooLong",
    "SanitizeResult",
    "contains_pii",
    "sanitize_for_llm",
    "sanitize_variables",
    "sanitize_with_report",
]
