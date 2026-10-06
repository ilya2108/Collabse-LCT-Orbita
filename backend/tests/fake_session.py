"""Сценарная подмена `AsyncSession` для тестов роутеров без PostgreSQL.

Сервисы auth/candidates обращаются к сессии через узкий набор операций: `execute(...)`
→ `.scalar_one_or_none()`, `get(Model, pk)`, `add`, `flush`, `commit`, `begin_nested`.
`FakeSession` отвечает на `execute` значениями из очереди `scalars` в порядке вызовов,
`get` — из словаря `rows`; `flush` проставляет `id`/`created_at`/`updated_at` новым
объектам и может по сценарию бросить IntegrityError (коллизия псевдонима).
"""

from __future__ import annotations

import uuid
from collections import deque
from collections.abc import Iterable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError


class _Result:
    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value

    def scalar(self) -> Any:
        return self._value

    def scalars(self) -> _Result:
        return self

    def all(self) -> list[Any]:
        return list(self._value or [])

    def first(self) -> Any:
        if isinstance(self._value, list):
            return self._value[0] if self._value else None
        return self._value


def integrity_error(detail: str) -> IntegrityError:
    return IntegrityError("INSERT ...", {}, Exception(detail))


class FakeSession:
    def __init__(
        self,
        scalars: Iterable[Any] = (),
        rows: dict[tuple[type, Any], Any] | None = None,
        flush_errors: Iterable[IntegrityError | None] = (),
    ) -> None:
        self.scalars = deque(scalars)
        self.rows = rows or {}
        self.flush_errors = deque(flush_errors)
        self.added: list[Any] = []
        self.commits = 0
        self.rollbacks = 0
        self.statements: list[Any] = []

    async def execute(self, statement: Any, params: Any = None) -> _Result:
        self.statements.append(statement)
        if not self.scalars:
            raise AssertionError("FakeSession: незапланированный execute()")
        return _Result(self.scalars.popleft())

    async def get(self, model: type, pk: Any) -> Any:
        return self.rows.get((model, pk))

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        if self.flush_errors:
            error = self.flush_errors.popleft()
            if error is not None:
                raise error
        now = datetime.now(UTC)
        for obj in self.added:
            if getattr(obj, "id", None) is None and hasattr(obj, "id"):
                obj.id = uuid.uuid4()
            for attr in ("created_at", "updated_at"):
                if hasattr(obj, attr) and getattr(obj, attr, None) is None:
                    setattr(obj, attr, now)

    async def commit(self) -> None:
        await self.flush()
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    @asynccontextmanager
    async def begin_nested(self):
        try:
            yield self
        except IntegrityError:
            # savepoint откатывается: объекты, добавленные внутри, отбрасываются
            self.added.clear()
            raise

    def added_of(self, model: type) -> list[Any]:
        return [obj for obj in self.added if isinstance(obj, model)]
