"""Проверка «схема БД соответствует head Alembic» — для healthcheck'а и ожидания миграций.

Использование: `python -m app.tools.check_migrations` → exit 0, если applied == head;
exit 1, если БД недоступна, таблицы alembic_version нет или ревизия отстаёт
(в compose backend ждёт, пока сервис `migrate` доведёт схему).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text

import app
from app.core.db import dispose_engine, get_engine


def script_head() -> str | None:
    backend_root = Path(app.__file__).resolve().parent.parent
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head()


async def applied_revision() -> str | None:
    async with get_engine().connect() as conn:
        result = await conn.execute(text("SELECT version_num FROM alembic_version"))
        return result.scalar_one_or_none()


async def main() -> int:
    head = script_head()
    if head is None:
        print("check_migrations: в alembic/versions нет ревизий", file=sys.stderr)
        return 1
    try:
        applied = await applied_revision()
    except Exception as exc:  # noqa: BLE001 — вызывающий ретраит любую причину
        print(f"check_migrations: БД недоступна или не мигрирована: {exc}", file=sys.stderr)
        return 1
    finally:
        await dispose_engine()
    if applied != head:
        print(
            f"check_migrations: applied={applied!r}, head={head!r} — ждём миграции",
            file=sys.stderr,
        )
        return 1
    print(f"check_migrations: ok (head={head})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
