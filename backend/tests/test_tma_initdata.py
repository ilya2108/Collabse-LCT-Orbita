"""Модуль tma: валидация initData Telegram.WebApp (чистая функция, реальный алгоритм).

Эталон собирается самим тестом тем же алгоритмом, что документирован Telegram:
`secret = HMAC_SHA256(key="WebAppData", msg=bot_token)`,
`hash = HMAC_SHA256(secret, data-check-string)`. Поля — со спецсимволами (кириллица,
кавычки, `&`, `=`, пробел) и в «upstream»-порядке, как присылает Telegram: `query_id`
и `user` раньше `auth_date`, без сортировки — сортировать обязана сама функция.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.modules.tma.deps import InitDataError, validate_init_data

BOT_TOKEN = "110201543:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
NOW = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

# поле user — JSON со спецсимволами: кириллица, кавычки, апостроф, &, =, пробел
USER_JSON = (
    '{"id":279058397,"first_name":"Пётр \\"О\'Генри\\"","last_name":"a&b=c",'
    '"username":"petr o","language_code":"ru"}'
)


def sign(fields: dict[str, str], token: str = BOT_TOKEN) -> str:
    """Подписывает поля алгоритмом Telegram и собирает query-строку в исходном порядке."""
    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


def upstream_fields(auth_date: datetime = NOW) -> dict[str, str]:
    """Поля в порядке, в котором их присылает Telegram (не отсортированы по алфавиту)."""
    return {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": USER_JSON,
        "auth_date": str(int(auth_date.timestamp())),
        "signature": "ed25519-signature-not-checked-here",
    }


class TestValidateInitData:
    def test_valid_returns_decoded_fields(self) -> None:
        raw = sign(upstream_fields())
        fields = validate_init_data(raw, BOT_TOKEN, NOW)
        assert fields["user"] == USER_JSON  # percent-кодирование снято без потерь
        assert fields["auth_date"] == str(int(NOW.timestamp()))
        assert "hash" not in fields

    def test_key_order_does_not_matter(self) -> None:
        """Та же подпись валидна при любом порядке пар в строке."""
        fields = upstream_fields()
        raw = sign(fields)
        reordered = urlencode(dict(sorted(fields.items(), reverse=True)))
        tail = raw.split("hash=")[1]
        assert validate_init_data(f"hash={tail}&{reordered}", BOT_TOKEN, NOW)["user"] == USER_JSON

    def test_broken_hash_rejected(self) -> None:
        raw = sign(upstream_fields())
        broken = raw[:-1] + ("0" if raw[-1] != "0" else "1")
        with pytest.raises(InitDataError):
            validate_init_data(broken, BOT_TOKEN, NOW)

    def test_wrong_bot_token_rejected(self) -> None:
        raw = sign(upstream_fields(), token="999999:another-bot-token")
        with pytest.raises(InitDataError):
            validate_init_data(raw, BOT_TOKEN, NOW)

    def test_tampered_field_rejected(self) -> None:
        """Подмена значения после подписи ломает подпись."""
        raw = sign(upstream_fields()).replace("query_id=AAH", "query_id=XXX")
        with pytest.raises(InitDataError):
            validate_init_data(raw, BOT_TOKEN, NOW)

    def test_missing_hash_rejected(self) -> None:
        with pytest.raises(InitDataError):
            validate_init_data(urlencode(upstream_fields()), BOT_TOKEN, NOW)

    def test_missing_auth_date_rejected(self) -> None:
        fields = upstream_fields()
        fields.pop("auth_date")
        with pytest.raises(InitDataError):
            validate_init_data(sign(fields), BOT_TOKEN, NOW)

    def test_expired_rejected(self) -> None:
        raw = sign(upstream_fields(auth_date=NOW - timedelta(hours=2)))
        with pytest.raises(InitDataError):
            validate_init_data(raw, BOT_TOKEN, NOW)

    def test_ttl_boundary(self) -> None:
        """Ровно на границе TTL строка ещё действительна, на секунду позже — нет."""
        raw = sign(upstream_fields(auth_date=NOW - timedelta(seconds=3600)))
        assert validate_init_data(raw, BOT_TOKEN, NOW, ttl_seconds=3600)
        with pytest.raises(InitDataError):
            validate_init_data(raw, BOT_TOKEN, NOW, ttl_seconds=3599)

    def test_empty_string_rejected(self) -> None:
        with pytest.raises(InitDataError):
            validate_init_data("", BOT_TOKEN, NOW)
