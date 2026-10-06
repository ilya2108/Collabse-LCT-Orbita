"""Демо-сиды и `/internal/demo/reset`: чистые помощники сидов (детерминизм, форматы)
и защита внутреннего эндпоинта токеном. Сам пересев требует PostgreSQL и проверяется
на стенде (`make demo` / POST /internal/demo/reset)."""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from app.modules.matching.seed import (
    GRADE_NODES,
    SYNTHETIC_COUNT,
    decayed,
    grade_for,
    pseudonym_for,
)
from tests.conftest import INTERNAL_TOKEN

PSEUDONYM_RE = re.compile(r"^[A-HJ-NP-Z]-[0-9A-F]{4}$")


class TestSeedHelpers:
    def test_pseudonyms_valid_and_unique(self) -> None:
        values = [pseudonym_for(i) for i in range(SYNTHETIC_COUNT)]
        assert len(set(values)) == SYNTHETIC_COUNT
        assert all(PSEUDONYM_RE.match(v) for v in values)

    def test_grades_cover_all(self) -> None:
        grades = {grade_for(i) for i in range(SYNTHETIC_COUNT)}
        assert grades == {"junior", "middle", "senior"}

    def test_decay_monotonic(self) -> None:
        assert decayed(1.0, 0) == 1.0
        assert decayed(1.0, 180) == 0.5
        assert decayed(1.0, 30) > decayed(1.0, 120)

    def test_grade_nodes_grow_with_grade(self) -> None:
        assert len(GRADE_NODES["junior"]) < len(GRADE_NODES["middle"])
        assert len(GRADE_NODES["middle"]) < len(GRADE_NODES["senior"])
        # все узлы — из специализации be
        for nodes in GRADE_NODES.values():
            assert all(node_id.startswith("be.") for node_id, _ in nodes)


class TestDemoResetEndpoint:
    def test_requires_internal_token(self, client: TestClient) -> None:
        assert client.post("/api/v1/internal/demo/reset").status_code == 401
        response = client.post("/api/v1/internal/demo/reset", headers={"X-Internal-Token": "wrong"})
        assert response.status_code == 401

    def test_token_header_accepted(self, client: TestClient) -> None:
        # с верным токеном запрос проходит аутентификацию; БД в тестах нет, поэтому
        # дальше ручка падает в 500 — важно, что это не 401 (контракт токена)
        response = client.post(
            "/api/v1/internal/demo/reset", headers={"X-Internal-Token": INTERNAL_TOKEN}
        )
        assert response.status_code != 401
