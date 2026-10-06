"""Эндпоинты /api/v1/skills/* через TestClient без БД и Keycloak (контракт CLAUDE.md)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.modules.skills.service import match_keywords
from app.skills.loader import load_catalog

BASE = "/api/v1/skills"


def test_industries(client: TestClient) -> None:
    response = client.get(f"{BASE}/industries")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 8
    assert all(set(item) == {"code", "title"} for item in body)
    assert {"code": "fintech", "title": "Финтех"} in body


def test_specializations(client: TestClient) -> None:
    response = client.get(f"{BASE}/specializations")
    assert response.status_code == 200
    body = response.json()
    assert [item["code"] for item in body] == ["be", "fe", "data"]
    for item in body:
        assert set(item) == {"code", "title", "status", "graph_version", "node_count"}
        assert item["node_count"] == 13
    statuses = {item["code"]: item["status"] for item in body}
    assert statuses == {"be": "active", "fe": "active", "data": "preview"}


def test_graph_be(client: TestClient) -> None:
    response = client.get(f"{BASE}/graph/be")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"specialization", "layers", "nodes", "edges", "bridges"}
    assert body["specialization"] == {
        "code": "be",
        "title": "Бэкенд",
        "status": "active",
        "graph_version": "1.0.0",
    }
    assert body["layers"] == [
        {"code": "D", "title": "Глубина"},
        {"code": "B", "title": "База"},
        {"code": "C", "title": "Контекст"},
    ]
    node_ids = {node["id"] for node in body["nodes"]}
    assert len(node_ids) == 13
    first = body["nodes"][0]
    assert set(first) == {"id", "code", "layer", "title", "description"}
    assert first == {
        "id": "be.D1",
        "code": "D1",
        "layer": "D",
        "title": "Язык и рантайм",
        "description": "типизация, модель памяти, async/await, стандартная библиотека",
    }
    assert len(body["edges"]) == 30
    for edge in body["edges"]:
        assert edge["kind"] in {"vertical", "lateral"}
        assert {edge["source"], edge["target"]} <= node_ids
    # 4 моста be–fe (§3.2) + 4 входящих моста из preview-графа data
    assert len(body["bridges"]) == 8
    assert all(b["kind"] == "bridge" for b in body["bridges"])
    assert all("be." in b["source"] + b["target"] for b in body["bridges"])
    be_fe = [b for b in body["bridges"] if b["target"].startswith("fe.")]
    assert len(be_fe) == 4
    assert {"source": "be.B1", "target": "fe.C2", "kind": "bridge"} in be_fe
    assert {"source": "data.D4", "target": "be.D4", "kind": "bridge"} in body["bridges"]


def test_graph_fe_includes_incoming_bridges(client: TestClient) -> None:
    body = client.get(f"{BASE}/graph/fe").json()
    assert len(body["edges"]) == 27
    assert {"source": "be.C1", "target": "fe.C1", "kind": "bridge"} in body["bridges"]
    assert body["bridges"] == [b for b in body["bridges"] if "fe." in b["source"] + b["target"]]


def test_graph_unknown_spec_returns_404_contract(client: TestClient) -> None:
    response = client.get(f"{BASE}/graph/qa")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert "qa" in body["message"]
    assert body["details"] == []
    assert body["request_id"] == response.headers["X-Request-Id"]


def test_grade_profiles_be(client: TestClient) -> None:
    response = client.get(f"{BASE}/grade-profiles/be")
    assert response.status_code == 200
    body = response.json()
    assert [p["grade"] for p in body] == ["junior", "middle", "senior"]
    junior = body[0]
    assert set(junior) == {
        "grade",
        "target_nodes",
        "task_level",
        "min_active",
        "min_q",
        "constraints",
        "budget",
        "demo",
    }
    assert junior["target_nodes"] == ["be.B1", "be.B2", "be.B3", "be.D1"]
    assert junior["task_level"] == 1 and junior["min_active"] == 3 and junior["min_q"] == 2
    assert junior["constraints"] == {}
    assert junior["budget"] == {"min_items": 4, "max_items": 8}
    assert junior["demo"] == {
        "target_nodes": ["be.B1", "be.B2", "be.D1"],
        "min_active": 2,
        "min_q": 1,
        "constraints": {},
    }
    middle, senior = body[1], body[2]
    assert middle["constraints"] == {"outside_layer": {"layer": "B", "min_active": 1}}
    assert middle["budget"] == {"min_items": 7, "max_items": 11}
    assert senior["constraints"] == {"layers_min_active": {"D": 1, "C": 1}}
    assert senior["budget"] == {"min_items": 10, "max_items": 12}
    assert senior["demo"]["target_nodes"] == ["be.B1", "be.B4", "be.D4", "be.C2", "be.C3"]


def test_grade_profiles_preview_spec_is_empty_list(client: TestClient) -> None:
    response = client.get(f"{BASE}/grade-profiles/data")
    assert response.status_code == 200 and response.json() == []


def test_grade_profiles_unknown_spec(client: TestClient) -> None:
    response = client.get(f"{BASE}/grade-profiles/nope")
    assert response.status_code == 404 and response.json()["code"] == "not_found"


def test_endpoints_are_public(client: TestClient) -> None:
    # справочник без ПДн читается без токена
    for path in ("industries", "specializations", "graph/be", "grade-profiles/fe"):
        assert client.get(f"{BASE}/{path}").status_code == 200


def test_match_keywords_rules_mode() -> None:
    catalog = load_catalog()
    text = (
        "Ищем бэкенд-разработчика на Python: проектирование REST API, PostgreSQL и индексы, "
        "Docker и CI/CD, опыт с Kafka."
    )
    match = match_keywords(text, catalog)
    assert match.specialization == "be"
    assert "python" in match.nodes["be.D1"]
    assert "rest" in match.nodes["be.B1"] and "api" in match.nodes["be.B1"]
    assert "postgresql" in match.nodes["be.D4"] and "индекс" in match.nodes["be.D4"]
    assert {"docker", "ci/cd"} <= set(match.nodes["be.C1"])
    assert "kafka" in match.nodes["be.C2"]
    assert match.unsupported == []
    # короткие слова — только целиком: «js» не ловится внутри «json»
    assert "js" not in match_keywords("парсим json-ответы", catalog).nodes.get("fe.D1", [])


def test_match_keywords_detects_unsupported_specialization() -> None:
    catalog = load_catalog()
    match = match_keywords("Нужен iOS-разработчик (Swift, SwiftUI)", catalog)
    assert [hit.code for hit in match.unsupported] == ["mobile"]
    assert match.unsupported[0].nearest == "fe"
    assert match.nodes == {} and match.specialization is None
