"""Шаблоны заданий: 8 эталонов загружаются и валидны; загрузчик ловит ошибки формата;
`GET /assessment/templates` отдаёт метаданные без ключей и эталонных ответов."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.modules.assessment.templates import (
    DETERMINISTIC_SHARE,
    CodeReviewKey,
    TaskTemplate,
    TemplateLoadError,
    get_catalog,
    get_template,
    iter_template_files,
    list_templates,
    load_catalog,
    parse_template_file,
    read_graph_node_ids,
    reset_catalog_cache,
)
from tests.conftest import INTERNAL_TOKEN, as_user, make_user

REFERENCE_IDS = {
    "be.B1.L1.idempotent_retry",
    "be.B1.L2.idempotency_key",
    "be.B1.L3.contract_evolution",
    "be.D4.L2.composite_index",
    "be.B3.L1.unique_constraint",
    "be.B3.L2.n_plus_one",
    "fe.B1.L1.lift_state",
    "fe.B1.L2.props_to_state_copy",
}

# §4.1: формулы оценки и ограничения на длину фрагмента
EXPECTED_KINDS = {"mcq_reasoned", "code_review", "short_design"}


@pytest.fixture()
def skills_path() -> Path:
    return get_settings().skills_path


@pytest.fixture()
def reference_catalog(skills_path: Path):
    return load_catalog(skills_path)


# --- эталонные шаблоны ---------------------------------------------------------------------------


def test_reference_templates_present_and_valid(reference_catalog) -> None:
    ids = set(reference_catalog.ids)
    assert REFERENCE_IDS <= ids, REFERENCE_IDS - ids
    assert len(ids) == len(reference_catalog.templates)  # id уникальны
    assert {t.kind for t in reference_catalog.templates} >= EXPECTED_KINDS


def test_reference_templates_cover_required_cells(reference_catalog) -> None:
    cells = {(t.node, t.level) for t in reference_catalog.templates}
    assert {
        ("be.B1", 1),
        ("be.B1", 2),
        ("be.B1", 3),
        ("be.D4", 2),
        ("be.B3", 1),
        ("be.B3", 2),
        ("fe.B1", 1),
        ("fe.B1", 2),
    } <= cells


def test_each_reference_template_satisfies_format_rules(reference_catalog) -> None:
    for t in reference_catalog.templates:
        assert abs(sum(c.w for c in t.rubric) - 1.0) < 1e-6, t.id
        assert 1 <= t.level <= 3
        assert t.anchor_variant.estimated_minutes * 60 <= t.max_time_sec, t.id
        assert t.difficulty_anchor.p_junior <= t.difficulty_anchor.p_middle, t.id
        assert t.deterministic_share == DETERMINISTIC_SHARE[t.kind]
        if t.kind == "mcq_reasoned":
            options = t.anchor_variant.options or []
            assert len(options) == 4, t.id
            assert sorted(o.key for o in options) == ["A", "B", "C", "D"], t.id
            assert t.anchor_variant.answer_key in {"A", "B", "C", "D"}, t.id
        elif t.kind == "code_review":
            key = t.anchor_variant.answer_key
            assert isinstance(key, CodeReviewKey), t.id
            assert t.defect_class, t.id
            assert 15 <= t.anchor_variant.code_line_count <= 40, t.id
            assert key.accepts(key.line)
        else:
            assert t.anchor_variant.answer_key is None and t.anchor_variant.options is None


def test_code_review_keys_point_at_the_defect_line(reference_catalog) -> None:
    """Номера строк в ключе указывают именно на строки с дефектом (защита от сдвига)."""
    n1 = reference_catalog.by_id("be.B3.L2.n_plus_one")
    lines = n1.anchor_variant.code.rstrip("\n").split("\n")
    assert "order.customer." in lines[n1.anchor_variant.answer_key.line - 1]
    assert n1.anchor_variant.answer_key.accepts(9) and "select(Order)" in lines[8]

    fe = reference_catalog.by_id("fe.B1.L2.props_to_state_copy")
    lines = fe.anchor_variant.code.rstrip("\n").split("\n")
    assert "useState<Product[]>(products)" in lines[fe.anchor_variant.answer_key.line - 1]


def test_file_layout_matches_identity(skills_path: Path) -> None:
    files = iter_template_files(skills_path)
    assert len(files) >= 8
    for path in files:
        t = parse_template_file(path, skills_path)
        spec, _, node_code, filename = path.relative_to(skills_path).parts
        assert t.node == f"{spec}.{node_code}"
        assert filename == f"L{t.level}_{t.slug}.yaml"


def test_list_and_get_templates(reference_catalog) -> None:
    be_b1 = list_templates("be", "be.B1", catalog=reference_catalog)
    assert [t.level for t in be_b1] == [1, 2, 3]
    assert [t.id for t in list_templates(level=3, catalog=reference_catalog)] == [
        "be.B1.L3.contract_evolution",
        "be.C2.L3.dual_write_outbox",
    ]
    assert len(list_templates("fe", catalog=reference_catalog)) >= 2
    assert list_templates("qa", catalog=reference_catalog) == []
    assert list_templates(kind="code_review", catalog=reference_catalog)
    assert list_templates(status="reviewed", catalog=reference_catalog) == [
        t for t in reference_catalog.templates if t.status == "reviewed"
    ]
    assert get_template("be.D4.L2.composite_index", catalog=reference_catalog) is not None
    assert get_template("be.D4.L9.nope", catalog=reference_catalog) is None


def test_get_catalog_is_cached(skills_path: Path) -> None:
    reset_catalog_cache()
    first = get_catalog(skills_path)
    assert get_catalog(skills_path) is first
    reset_catalog_cache()
    assert get_catalog(skills_path) is not first


# --- негативные сценарии загрузчика ----------------------------------------------------


def _write_template(root: Path, data: dict, filename: str | None = None) -> Path:
    spec, code = data["node"].split(".")
    slug = data["id"].rsplit(".", 1)[1]
    directory = root / spec / "templates" / code
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or f"L{data['level']}_{slug}.yaml")
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _copy_reference(skills_path: Path, tmp_root: Path, template_id: str) -> dict:
    source = next(p for p in iter_template_files(skills_path) if template_id in p.read_text())
    data = yaml.safe_load(source.read_text(encoding="utf-8"))
    assert data["id"] == template_id
    return data


def test_invalid_templates_are_collected_with_paths(skills_path: Path, tmp_path: Path) -> None:
    base = _copy_reference(skills_path, tmp_path, "be.B3.L1.unique_constraint")

    bad_weights = dict(base, id="be.B3.L1.bad_weights")
    bad_weights["rubric"] = [dict(c, w=0.5) for c in base["rubric"]]
    _write_template(tmp_path, bad_weights)

    three_options = dict(base, id="be.B3.L1.three_options")
    three_options["anchor_variant"] = dict(
        base["anchor_variant"], options=base["anchor_variant"]["options"][:3]
    )
    _write_template(tmp_path, three_options)

    wrong_key = dict(base, id="be.B3.L1.wrong_key")
    wrong_key["anchor_variant"] = dict(base["anchor_variant"], answer_key="E")
    _write_template(tmp_path, wrong_key)

    bad_level = dict(base, id="be.B3.L4.bad_level", level=4)
    _write_template(tmp_path, bad_level, filename="L1_bad_level.yaml")

    node_mismatch = dict(base, id="be.B3.L1.node_mismatch")
    _write_template(tmp_path, node_mismatch)
    moved = tmp_path / "be" / "templates" / "B4" / "L1_node_mismatch.yaml"
    moved.parent.mkdir(parents=True)
    shutil.move(tmp_path / "be" / "templates" / "B3" / "L1_node_mismatch.yaml", moved)

    unknown_field = dict(base, id="be.B3.L1.unknown_field", answer="подсказка")
    _write_template(tmp_path, unknown_field)

    with pytest.raises(TemplateLoadError) as excinfo:
        load_catalog(tmp_path)
    problems = {Path(p.path).name: p.message for p in excinfo.value.problems}
    assert "L1_bad_weights.yaml" in problems and "сумма весов" in problems["L1_bad_weights.yaml"]
    assert "4 опции" in problems["L1_three_options.yaml"]
    assert "answer_key" in problems["L1_wrong_key.yaml"]
    assert "level" in problems["L1_bad_level.yaml"]
    assert "не совпадает с каталогом" in problems["L1_node_mismatch.yaml"]
    assert "answer" in problems["L1_unknown_field.yaml"]
    # все проблемы перечислены разом, с путями
    assert len(problems) == 6
    assert all(Path(p.path).is_absolute() for p in excinfo.value.problems)


def test_duplicate_id_is_rejected(skills_path: Path, tmp_path: Path) -> None:
    base = _copy_reference(skills_path, tmp_path, "be.B3.L1.unique_constraint")
    _write_template(tmp_path, base)
    _write_template(tmp_path, base, filename="L1_unique_constraint_copy.yaml")
    with pytest.raises(TemplateLoadError) as excinfo:
        load_catalog(tmp_path)
    messages = " ".join(p.message for p in excinfo.value.problems)
    assert "уже объявлен" in messages or "slug" in messages


def test_code_review_rules(skills_path: Path, tmp_path: Path) -> None:
    base = _copy_reference(skills_path, tmp_path, "be.B3.L2.n_plus_one")
    too_short = dict(base, id="be.B3.L2.too_short")
    too_short["anchor_variant"] = dict(base["anchor_variant"], code="x = 1\ny = 2\n")
    _write_template(tmp_path, too_short)
    line_out = dict(base, id="be.B3.L2.line_out")
    line_out["anchor_variant"] = dict(base["anchor_variant"], answer_key={"line": 99})
    _write_template(tmp_path, line_out)
    no_class = dict(base, id="be.B3.L2.no_class", defect_class=None)
    _write_template(tmp_path, no_class)
    with pytest.raises(TemplateLoadError) as excinfo:
        load_catalog(tmp_path)
    problems = {Path(p.path).name: p.message for p in excinfo.value.problems}
    assert "15–40 строк" in problems["L2_too_short.yaml"]
    assert "вне фрагмента" in problems["L2_line_out.yaml"]
    assert "defect_class" in problems["L2_no_class.yaml"]


def test_graph_check_skipped_with_warning_or_enforced(skills_path: Path, tmp_path: Path) -> None:
    base = _copy_reference(skills_path, tmp_path, "fe.B1.L1.lift_state")
    _write_template(tmp_path, base)

    # графа нет — предупреждение, шаблон загружен
    catalog = load_catalog(tmp_path)
    assert catalog.ids == ["fe.B1.L1.lift_state"]
    assert any("graph.yaml" in w for w in catalog.warnings)

    # граф есть, узел в нём отсутствует — ошибка
    graph = {"code": "fe", "nodes": [{"id": "fe.D1", "code": "D1"}, {"code": "B2"}]}
    (tmp_path / "fe" / "graph.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    assert read_graph_node_ids(tmp_path, "fe") == {"fe.D1", "fe.B2"}
    with pytest.raises(TemplateLoadError) as excinfo:
        load_catalog(tmp_path)
    assert "отсутствует в графе" in excinfo.value.problems[0].message

    # узел добавлен — загрузка без предупреждений
    graph["nodes"].append({"id": "fe.B1"})
    (tmp_path / "fe" / "graph.yaml").write_text(yaml.safe_dump(graph), encoding="utf-8")
    catalog = load_catalog(tmp_path)
    assert catalog.ids == ["fe.B1.L1.lift_state"] and catalog.warnings == ()


def test_model_rejects_defect_class_outside_code_review(reference_catalog) -> None:
    data = reference_catalog.by_id("fe.B1.L1.lift_state").model_dump()
    data["defect_class"] = "n_plus_one"
    with pytest.raises(ValueError, match="defect_class"):
        TaskTemplate.model_validate(data)


# --- роутер --------------------------------------------------------------------------------------


def _assert_no_answers(payload: object) -> None:
    forbidden = {"answer_key", "reference_answer", "anchor_variant", "statement", "options", "code"}
    if isinstance(payload, dict):
        assert not (forbidden & set(payload)), forbidden & set(payload)
        for value in payload.values():
            _assert_no_answers(value)
    elif isinstance(payload, list):
        for item in payload:
            _assert_no_answers(item)


def test_templates_endpoint_requires_admin(client: TestClient, app) -> None:
    assert client.get("/api/v1/assessment/templates").status_code == 401
    with as_user(app, make_user(roles=["candidate"])):
        response = client.get("/api/v1/assessment/templates")
        assert response.status_code == 403 and response.json()["code"] == "forbidden"
    with as_user(app, make_user(roles=["employer"])):
        assert client.get("/api/v1/assessment/templates").status_code == 403


def test_templates_endpoint_returns_metadata_without_answers(client: TestClient, app) -> None:
    with as_user(app, make_user(roles=["admin"])):
        response = client.get("/api/v1/assessment/templates")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["total"] == len(body["items"]) >= 8
        assert REFERENCE_IDS <= {item["id"] for item in body["items"]}
        _assert_no_answers(body)
        item = next(i for i in body["items"] if i["id"] == "be.B3.L2.n_plus_one")
        assert item["kind"] == "code_review" and item["defect_class"] == "n_plus_one"
        assert item["deterministic_share"] == 0.5 and item["estimated_minutes"] == 3
        assert item["rubric"][0]["id"] == "cause"

        filtered = client.get(
            "/api/v1/assessment/templates", params={"spec": "be", "node": "be.B1", "level": 2}
        ).json()
        assert [i["id"] for i in filtered["items"]] == ["be.B1.L2.idempotency_key"]
        assert client.get("/api/v1/assessment/templates", params={"spec": "qa"}).json() == {
            "items": [],
            "total": 0,
            "warnings": filtered["warnings"],
        }
        bad = client.get("/api/v1/assessment/templates", params={"level": 7})
        assert bad.status_code == 400 and bad.json()["code"] == "validation_error"


def test_templates_endpoint_internal_token(client: TestClient) -> None:
    assert client.get("/api/v1/assessment/internal/templates").status_code == 401
    response = client.get(
        "/api/v1/assessment/internal/templates",
        params={"node": "fe.B1"},
        headers={"X-Internal-Token": INTERNAL_TOKEN},
    )
    assert response.status_code == 200
    assert {i["id"] for i in response.json()["items"]} >= {
        "fe.B1.L1.lift_state",
        "fe.B1.L2.props_to_state_copy",
    }
    _assert_no_answers(response.json())
