"""Роутер /llm: usage по внутреннему токену или admin-токену, dry-run и prompts — admin."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.security import TokenClaims
from app.modules.llm.router import optional_token_claims
from tests.conftest import INTERNAL_TOKEN, as_user, make_user
from tests.test_llm_helpers import NEED_VARS, reset_llm_state


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_llm_state()


@pytest.fixture()
def with_claims(app: FastAPI):
    def _apply(roles: tuple[str, ...]) -> Iterator[None]:
        claims = TokenClaims(
            sub="11111111-1111-1111-1111-111111111111",
            username="admin",
            email=None,
            email_verified=True,
            roles=roles,
        )
        app.dependency_overrides[optional_token_claims] = lambda: claims
        return None

    yield _apply
    app.dependency_overrides.pop(optional_token_claims, None)


def test_usage_requires_internal_token_or_admin(client: TestClient, with_claims) -> None:
    assert client.get("/api/v1/llm/usage").status_code == 401
    wrong = client.get("/api/v1/llm/usage", headers={"X-Internal-Token": "wrong"})
    assert wrong.status_code == 401 and wrong.json()["code"] == "unauthorized"

    ok = client.get("/api/v1/llm/usage", headers={"X-Internal-Token": INTERNAL_TOKEN})
    assert ok.status_code == 200
    body = ok.json()
    assert body["provider"] == "stub" and body["today"]["daily_cap"] > 0
    assert body["queue"]["limit"] >= 1 and body["recent"] == []
    assert ok.headers["X-Request-Id"]

    with_claims(("candidate",))
    assert client.get("/api/v1/llm/usage").status_code == 403
    with_claims(("admin", "candidate"))
    assert client.get("/api/v1/llm/usage").status_code == 200


def test_dry_run_admin_only(client: TestClient, app: FastAPI) -> None:
    body = {"prompt_name": "need_parse", "variables": NEED_VARS}
    assert client.post("/api/v1/llm/dry-run", json=body).status_code == 401
    with as_user(app, make_user(roles=["candidate"])):
        assert client.post("/api/v1/llm/dry-run", json=body).status_code == 403
    with as_user(app, make_user(roles=["admin"])):
        response = client.post("/api/v1/llm/dry-run", json=body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["prompt_name"] == "need_parse" and data["prompt_version"] == "v1"
        assert data["schema_name"] == "NeedProfile" and data["model"] == "stub-fixture"
        assert data["result"]["specialization"] == "be"
        assert data["messages"][0]["role"] == "system" and len(data["prompt_hash"]) == 64

        missing = client.post(
            "/api/v1/llm/dry-run", json={"prompt_name": "need_parse", "variables": {}}
        )
        assert missing.status_code == 400 and missing.json()["code"] == "validation_error"
        unknown = client.post("/api/v1/llm/dry-run", json={"prompt_name": "nope"})
        assert unknown.status_code == 400 and "nope" in unknown.json()["message"]

        pii = client.post(
            "/api/v1/llm/dry-run",
            json={
                "prompt_name": "need_parse",
                "variables": {**NEED_VARS, "need_text": "Контакт ivan@example.com, Python"},
            },
        )
        assert "ivan@example.com" not in pii.json()["messages"][1]["content"]
        assert "[email]" in pii.json()["messages"][1]["content"]


def test_prompts_list(client: TestClient, app: FastAPI) -> None:
    with as_user(app, make_user(roles=["admin"])):
        response = client.get("/api/v1/llm/prompts")
    assert response.status_code == 200
    names = {p["name"] for p in response.json()}
    assert {"need_parse", "variant_generate", "variant_judge", "answer_grade"} <= names
    judge = next(p for p in response.json() if p["name"] == "variant_judge")
    assert judge["temperature"] == 0 and judge["tier"] == "strong"
    assert judge["variables"] == ["template", "anchor", "variant"]
