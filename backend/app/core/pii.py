"""Утилиты защиты ПДн (152-ФЗ): шифрование Fernet, blind index HMAC, псевдонимизация.

Схема (PLAN.md §7.4, §8):
- значения шифруются Fernet'ом (MultiFernet: первый ключ активный, остальные читают старое);
- поиск по точному значению — HMAC-SHA256 от нормализованного значения отдельным ключом
  (колонки `*_hash`);
- в списках, кэше, логах и вызовах LLM — только псевдоним кандидата («B-7F3A») или
  маскированное значение; расшифровка контактов — только через явный сервис с аудитом.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import unicodedata
from collections.abc import Sequence

from cryptography.fernet import Fernet, MultiFernet
from sqlalchemy import LargeBinary
from sqlalchemy.types import TypeDecorator

_SPACES_RE = re.compile(r"\s+")
_NON_DIGITS_RE = re.compile(r"\D+")


class PiiConfigurationError(RuntimeError):
    """Ключи шифрования ПДн не сконфигурированы (PII_FERNET_KEYS / PII_HMAC_KEY)."""


# --------------------------------------------------------------------------------------
# Нормализация перед HMAC — иначе поиск/дедупликация развалятся
# --------------------------------------------------------------------------------------


def normalize_email(raw: str) -> str:
    """email → strip().lower(), NFC."""
    return unicodedata.normalize("NFC", raw.strip().lower())


def normalize_full_name(raw: str) -> str:
    """ФИО → trim, схлопнуть повторные пробелы, lower, ё→е, Unicode NFC."""
    value = _SPACES_RE.sub(" ", raw.strip()).lower()
    value = value.replace("ё", "е")
    return unicodedata.normalize("NFC", value)


def normalize_phone(raw: str) -> str:
    """Телефон → только цифры; российский «8…» приводится к «7…» (11 цифр)."""
    digits = _NON_DIGITS_RE.sub("", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


# нормализаторы по имени атрибута — используются rekey и доменными сервисами
NORMALIZERS = {
    "email": normalize_email,
    "full_name": normalize_full_name,
    "phone": normalize_phone,
    "contact_email": normalize_email,
    "contact_phone": normalize_phone,
}


def normalize_for(attr: str, raw: str) -> str:
    """Нормализация по имени атрибута; для неизвестных — trim + lower + NFC."""
    normalizer = NORMALIZERS.get(attr)
    if normalizer is not None:
        return normalizer(raw)
    return unicodedata.normalize("NFC", raw.strip().lower())


# --------------------------------------------------------------------------------------
# Псевдонимизация и маскирование
# --------------------------------------------------------------------------------------

_PSEUDONYM_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ"  # без I и O — не путаются с 1 и 0
_PSEUDONYM_HEX = "0123456789ABCDEF"


def generate_pseudonym() -> str:
    """Псевдоним кандидата вида «B-7F3A»: буква + 4 hex-символа; уникальность
    обеспечивает UNIQUE в БД (при коллизии вызывающий код генерирует заново)."""
    letter = secrets.choice(_PSEUDONYM_ALPHABET)
    tail = "".join(secrets.choice(_PSEUDONYM_HEX) for _ in range(4))
    return f"{letter}-{tail}"


def display_name_from_full_name(full_name: str) -> str:
    """Псевдоним «Фамилия И.О.» из полного ФИО (генерируется до шифрования).

    «Иванов Иван Иванович» → «Иванов И.И.», «Иванов Иван» → «Иванов И.», «Иванов» → «Иванов».
    """
    parts = _SPACES_RE.sub(" ", full_name.strip()).split(" ")
    if not parts or not parts[0]:
        return ""
    surname = parts[0]
    initials = "".join(f"{p[0].upper()}." for p in parts[1:3] if p)
    return f"{surname} {initials}".strip()


def _mask_word(word: str) -> str:
    if len(word) <= 2:
        return f"{word[:1]}***"
    return f"{word[0]}***{word[-1]}"


def mask_full_name(full_name: str) -> str:
    """«Иванов Иван Иванович» → «И***в И.И.»."""
    parts = _SPACES_RE.sub(" ", full_name.strip()).split(" ")
    if not parts or not parts[0]:
        return ""
    masked = _mask_word(parts[0])
    initials = "".join(f"{p[0].upper()}." for p in parts[1:3] if p)
    return f"{masked} {initials}".strip()


def mask_email(email: str) -> str:
    """«ivanov@example.com» → «i***v@example.com»; домен не скрывается."""
    value = email.strip()
    if "@" not in value:
        return _mask_word(value)
    local, _, domain = value.partition("@")
    return f"{_mask_word(local)}@{domain}"


def mask_phone(phone: str) -> str:
    """«+7 916 000-11-22» → «+7 *** *** 11-22» (последние 4 значимые цифры видимы)."""
    digits = [c for c in phone if c.isdigit()]
    if len(digits) < 4:
        return "***"
    return f"+{digits[0]} *** *** {''.join(digits[-4:-2])}-{''.join(digits[-2:])}"


# --------------------------------------------------------------------------------------
# Крипто-сервис
# --------------------------------------------------------------------------------------


class PiiCrypto:
    """Шифрование/blind-index ПДн. Ключи — из env PII_FERNET_KEYS / PII_HMAC_KEY."""

    def __init__(self, fernet_keys: Sequence[str], hmac_key: str) -> None:
        if not fernet_keys:
            raise PiiConfigurationError(
                "PII_FERNET_KEYS не задан: сгенерируйте ключ командой "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        if not hmac_key or len(hmac_key.encode("utf-8")) < 32:
            raise PiiConfigurationError(
                "PII_HMAC_KEY не задан или короче 32 байт: сгенерируйте ключ командой "
                "openssl rand -base64 32"
            )
        self._fernet = MultiFernet([Fernet(k) for k in fernet_keys])
        self._hmac_key = hmac_key.encode("utf-8")

    def encrypt(self, value: str) -> bytes:
        return self._fernet.encrypt(value.encode("utf-8"))

    def decrypt(self, token: bytes | memoryview) -> str:
        return self._fernet.decrypt(bytes(token)).decode("utf-8")

    def rotate(self, token: bytes | memoryview) -> bytes:
        """Перешифровать старый шифртекст активным (первым) ключом — для app.tools.rekey."""
        return self._fernet.rotate(bytes(token))

    def hmac_hex(self, normalized: str) -> str:
        """hex(HMAC-SHA256) от УЖЕ нормализованного значения (64 hex-символа)."""
        return hmac.new(self._hmac_key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()

    def blind_index(self, attr: str, raw: str) -> str:
        """Blind index по имени атрибута: нормализация `normalize_for(attr)` + HMAC."""
        return self.hmac_hex(normalize_for(attr, raw))

    def email_hmac(self, raw_email: str) -> str:
        return self.hmac_hex(normalize_email(raw_email))

    def full_name_hmac(self, raw_full_name: str) -> str:
        return self.hmac_hex(normalize_full_name(raw_full_name))

    def phone_hmac(self, raw_phone: str) -> str:
        return self.hmac_hex(normalize_phone(raw_phone))


_crypto: PiiCrypto | None = None


def get_pii_crypto() -> PiiCrypto:
    """Единственный экземпляр PiiCrypto на процесс (ленивая инициализация из Settings)."""
    global _crypto
    if _crypto is None:
        from app.core.config import get_settings

        settings = get_settings()
        _crypto = PiiCrypto(settings.pii_fernet_key_list, settings.pii_hmac_key)
    return _crypto


def reset_pii_crypto() -> None:
    """Сброс кэшированного экземпляра (тесты, ротация ключей)."""
    global _crypto
    _crypto = None


class EncryptedStr(TypeDecorator):
    """SQLAlchemy-тип: python `str` ↔ bytea с Fernet-шифртекстом.

    Атрибут модели хранит открытое значение только в памяти процесса; в БД — шифртекст.
    Паттерн: `email: Mapped[str | None] = mapped_column("email_enc", EncryptedStr)` +
    отдельная колонка `email_hash` (CHAR(64)), которую заполняет доменный сервис через
    `get_pii_crypto().blind_index("email", value)`.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: object) -> bytes | None:
        if value is None:
            return None
        return get_pii_crypto().encrypt(value)

    def process_result_value(self, value: bytes | None, dialect: object) -> str | None:
        if value is None:
            return None
        return get_pii_crypto().decrypt(value)
