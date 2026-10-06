"""Ротация ключей шифрования ПДн (152-ФЗ).

Использование: добавить новый Fernet-ключ ПЕРВЫМ в PII_FERNET_KEYS, перезапустить backend
и выполнить `python -m app.tools.rekey` — батчами перешифровывает все колонки типа
`EncryptedStr` активным ключом (SELECT ... FOR UPDATE SKIP LOCKED, по 500 строк).
Старый ключ удаляется из списка после полного прогона. При смене PII_HMAC_KEY blind-index
колонки `<attr>_hash` пересчитываются заново (расшифровка → нормализация → новый HMAC).

Таблицы и колонки находятся автоматически по метаданным моделей — новые шифрованные
поля (candidate_profile, company) подхватываются без правки этого файла.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.orm import Mapper
from sqlalchemy.orm.attributes import flag_modified

import app.models  # noqa: F401 — регистрирует все модели в Base.metadata
from app.core.db import dispose_engine, get_session_factory
from app.core.pii import EncryptedStr, get_pii_crypto
from app.models.base import Base

BATCH_SIZE = 500


def encrypted_targets() -> list[tuple[type, tuple[str, ...]]]:
    """[(модель, (атрибуты с EncryptedStr, …)), …] по всем смаппленным классам."""
    targets: list[tuple[type, tuple[str, ...]]] = []
    for mapper in Base.registry.mappers:
        attrs = tuple(
            prop.key
            for prop in mapper.column_attrs
            if len(prop.columns) == 1 and isinstance(prop.columns[0].type, EncryptedStr)
        )
        if attrs:
            targets.append((mapper.class_, attrs))
    return sorted(targets, key=lambda item: item[0].__tablename__)


def _hash_attrs(mapper: Mapper, attrs: tuple[str, ...]) -> dict[str, str]:
    """{атрибут: имя колонки blind-index} для атрибутов, у которых есть `<attr>_hash`."""
    known = {prop.key for prop in mapper.column_attrs}
    return {attr: f"{attr}_hash" for attr in attrs if f"{attr}_hash" in known}


async def _rekey_table(model: type, attrs: tuple[str, ...]) -> int:
    """Перешифровывает bytea-колонки модели активным ключом; возвращает число строк."""
    crypto = get_pii_crypto()
    factory = get_session_factory()
    hash_attrs = _hash_attrs(model.__mapper__, attrs)
    total = 0
    last_id = None
    while True:
        async with factory() as session:
            stmt = (
                select(model).order_by(model.id).limit(BATCH_SIZE).with_for_update(skip_locked=True)
            )
            if last_id is not None:
                stmt = stmt.where(model.id > last_id)
            rows = (await session.execute(stmt)).scalars().all()
            if not rows:
                return total
            for row in rows:
                for attr in attrs:
                    value: str | None = getattr(row, attr)
                    if value is None:
                        continue
                    # присваивание того же значения (даже через None) не делает атрибут
                    # «грязным»: SQLAlchemy сравнивает итог с загруженным открытым значением.
                    # flag_modified включает колонку в UPDATE, и TypeDecorator шифрует её
                    # заново активным (первым) ключом
                    setattr(row, attr, value)
                    flag_modified(row, attr)
                    hash_attr = hash_attrs.get(attr)
                    if hash_attr is not None:
                        setattr(row, hash_attr, crypto.blind_index(attr, value))
                total += 1
            last_id = rows[-1].id
            await session.commit()


async def main() -> int:
    results: list[str] = []
    for model, attrs in encrypted_targets():
        count = await _rekey_table(model, attrs)
        results.append(f"{model.__tablename__}={count}")
    await dispose_engine()
    print(f"rekey: перешифровано строк: {', '.join(results) or 'нет шифрованных таблиц'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
