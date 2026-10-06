"""Сид справочника навыков (app/modules/skills/seed.py): чистая часть без PostgreSQL —
строки для всех таблиц строятся из каталога YAML и согласованы с моделями §7.4.
"""

from __future__ import annotations

import pytest

from app.models.skills import EDGE_KINDS, GRADES, LAYERS, SPECIALIZATION_STATUSES
from app.modules.skills.seed import ANY_EVENT, collect_rows
from app.skills.loader import get_catalog


@pytest.fixture(scope="module")
def rows():
    return collect_rows(get_catalog())


def test_tables_and_counts(rows) -> None:
    catalog = get_catalog()
    assert set(rows) == {
        "industry",
        "specialization",
        "skill_node",
        "skill_edge",
        "grade_profile",
        "fsp_discipline_map",
    }
    assert len(rows["industry"]) == len(catalog.industries) >= 8
    assert [r["code"] for r in rows["specialization"]] == list(catalog.specializations)
    assert len(rows["skill_node"]) == 13 * len(catalog.specializations)
    expected_edges = sum(len(s.file.edges) for s in catalog.specializations.values())
    assert len(rows["skill_edge"]) == expected_edges + len(catalog.bridges)
    assert len(rows["grade_profile"]) == sum(
        len(catalog.profiles(code)) for code in catalog.specializations
    )
    assert len(rows["fsp_discipline_map"]) == len(catalog.fsp_mapping.disciplines)


def test_rows_match_db_constraints(rows) -> None:
    node_ids = {r["id"] for r in rows["skill_node"]}
    spec_codes = {r["code"] for r in rows["specialization"]}
    for spec in rows["specialization"]:
        assert spec["status"] in SPECIALIZATION_STATUSES
        assert spec["graph_version"]
    for node in rows["skill_node"]:
        assert node["layer"] in LAYERS
        assert node["specialization_code"] in spec_codes
        assert node["id"] == f"{node['specialization_code']}.{node['code']}"
        assert node["title"] and node["description"]
    # (specialization_code, code) уникальны — UNIQUE uq_skill_node_spec_code
    assert len({(n["specialization_code"], n["code"]) for n in rows["skill_node"]}) == len(
        rows["skill_node"]
    )
    keys = [(e["source_id"], e["target_id"]) for e in rows["skill_edge"]]
    assert len(set(keys)) == len(keys), "дубли рёбер нарушат PK (source_id, target_id)"
    for edge in rows["skill_edge"]:
        assert edge["kind"] in EDGE_KINDS
        assert edge["source_id"] != edge["target_id"]
        assert {edge["source_id"], edge["target_id"]} <= node_ids
        same_spec = edge["source_id"].split(".")[0] == edge["target_id"].split(".")[0]
        assert same_spec == (edge["kind"] != "bridge")


def test_grade_profiles_shape(rows) -> None:
    node_ids = {r["id"] for r in rows["skill_node"]}
    pairs = {(p["specialization_code"], p["grade"]) for p in rows["grade_profile"]}
    assert len(pairs) == len(rows["grade_profile"]), "UNIQUE (specialization_code, grade)"
    assert {g for _, g in pairs} == set(GRADES)
    for profile in rows["grade_profile"]:
        assert 1 <= profile["task_level"] <= 3
        assert set(profile["target_nodes"]) <= node_ids
        assert set(profile["budget"]) == {"min_items", "max_items"}
        assert profile["budget"]["min_items"] <= profile["budget"]["max_items"]
        assert set(profile["demo"]) == {"target_nodes", "min_active", "min_q", "constraints"}
        assert set(profile["demo"]["target_nodes"]) <= set(profile["target_nodes"])
        assert isinstance(profile["constraints"], dict)
    # формат constraints как в API: junior → {}, middle → outside_layer, senior → layers_min_active
    by_grade = {(p["specialization_code"], p["grade"]): p for p in rows["grade_profile"]}
    assert by_grade[("be", "junior")]["constraints"] == {}
    assert "outside_layer" in by_grade[("be", "middle")]["constraints"]
    assert "layers_min_active" in by_grade[("be", "senior")]["constraints"]


def test_fsp_discipline_rows(rows) -> None:
    node_ids = {r["id"] for r in rows["skill_node"]}
    spec_codes = {r["code"] for r in rows["specialization"]}
    keys = [(d["discipline"], d["event_type"]) for d in rows["fsp_discipline_map"]]
    assert len(set(keys)) == len(keys), "UNIQUE (discipline, event_type)"
    for row in rows["fsp_discipline_map"]:
        assert row["event_type"] == ANY_EVENT
        assert row["specialization_code"] is None or row["specialization_code"] in spec_codes
        assert row["level_rule"]["by"] in {"percentile", "tier"}
        assert row["level_rule"]["node_scope"] in {"fixed", "candidate_specialization"}
        for ref in row["node_weights"]:
            assert 0 < ref["weight"] <= 1
            if "node_id" in ref:
                assert ref["node_id"] in node_ids
            else:
                assert ref["node_code"]
