"""Идемпотентный seed демо-стенда (`python -m app.tools.seed`).

Каркас без данных: шаги сида регистрируют сами модули — каждый пакет `app.modules.<name>`
может содержать `seed.py` с корутиной `async def seed(session: AsyncSession) -> dict[str, int]`,
возвращающей счётчики («created», «updated», …). Шаги выполняются в порядке `SEED_ORDER`
(справочники раньше сущностей, которые на них ссылаются), каждый в своей транзакции.

Идемпотентность — upsert по натуральным ключам: повторный запуск ничего не дублирует.
ПДн демо-пользователей шифруются тем же кодом, что в проде (core/pii).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import dispose_engine, get_session_factory

SeedStep = Callable[[AsyncSession], Awaitable[dict[str, int]]]

# порядок модулей: справочники → пользователи → кандидаты → тестирование → работодатели
SEED_ORDER = (
    "skills",
    "auth",
    "candidates",
    "assessment",
    "llm",
    "employers",
    "matching",
    "fsp",
)


def discover_steps() -> list[tuple[str, SeedStep]]:
    """Находит `app.modules.<name>.seed:seed` для модулей из SEED_ORDER (если файл есть)."""
    steps: list[tuple[str, SeedStep]] = []
    for name in SEED_ORDER:
        try:
            module = importlib.import_module(f"app.modules.{name}.seed")
        except ModuleNotFoundError as exc:
            if exc.name != f"app.modules.{name}.seed":
                raise  # сломан импорт внутри модуля сида — не маскируем
            continue
        step = getattr(module, "seed", None)
        if step is not None:
            steps.append((name, step))
    return steps


async def run_seed() -> dict[str, dict[str, int]]:
    factory = get_session_factory()
    report: dict[str, dict[str, int]] = {}
    for name, step in discover_steps():
        async with factory() as session:
            report[name] = await step(session)
            await session.commit()
    return report


async def main() -> int:
    try:
        report = await run_seed()
    finally:
        await dispose_engine()
    if not report:
        print("seed: шагов нет (модули ещё не зарегистрировали seed.py) — ничего не изменено")
        return 0
    for name, counters in report.items():
        summary = ", ".join(f"{key}={value}" for key, value in counters.items()) or "ok"
        print(f"seed: {name}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
