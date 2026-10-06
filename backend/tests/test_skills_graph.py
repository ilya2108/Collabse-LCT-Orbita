"""Валидность YAML-справочника навыков: счётчики узлов/рёбер по PLAN.md §3.2, инварианты
структуры, связность целевых и демо-подграфов (§3.4, §4.3), словари ФСП и ключевых слов."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from app.core.config import get_settings
from app.modules.skills.strength import check_grade_profile, is_connected, q_level
from app.skills.loader import (
    SkillsCatalog,
    SkillsCatalogError,
    get_catalog,
    load_catalog,
    reset_catalog_cache,
)

SKILLS_DIR = Path(__file__).resolve().parents[1] / "app" / "skills"


@pytest.fixture(scope="module")
def catalog() -> SkillsCatalog:
    return load_catalog(SKILLS_DIR)


def _edge_set(catalog: SkillsCatalog, spec: str) -> set[frozenset[str]]:
    return {edge.key for edge in catalog.spec(spec).file.edges}


# --- специализации и графы -------------------------------------------------------------------


def test_catalog_lists_specializations_active_first(catalog: SkillsCatalog) -> None:
    codes = list(catalog.specializations)
    assert codes == ["be", "fe", "data"]
    assert catalog.spec("be").meta.status == "active"
    assert catalog.spec("fe").meta.status == "active"
    assert catalog.spec("data").meta.status == "preview"


@pytest.mark.parametrize("spec", ["be", "fe", "data"])
def test_each_graph_has_13_nodes_in_three_layers(catalog: SkillsCatalog, spec: str) -> None:
    graph = catalog.spec(spec)
    assert len(graph.file.nodes) == 13
    by_layer = dict.fromkeys("DBC", 0)
    for node in graph.file.nodes:
        by_layer[node.layer] += 1
        assert node.id == f"{spec}.{node.code}"
        assert node.description
    assert by_layer == {"D": 4, "B": 5, "C": 4}
    assert [layer.code for layer in graph.file.layers] == ["D", "B", "C"]


@pytest.mark.parametrize(
    ("spec", "vertical", "lateral"),
    [("be", 16, 14), ("fe", 14, 13), ("data", 17, 14)],
)
def test_edge_counts_by_kind(
    catalog: SkillsCatalog, spec: str, vertical: int, lateral: int
) -> None:
    edges = catalog.spec(spec).file.edges
    kinds = {kind: sum(1 for e in edges if e.kind == kind) for kind in ("vertical", "lateral")}
    assert kinds == {"vertical": vertical, "lateral": lateral}
    assert len({edge.key for edge in edges}) == len(edges)


def test_be_edges_match_plan(catalog: SkillsCatalog) -> None:
    expected = {
        ("D1", "B1"), ("D1", "B2"), ("D2", "B3"), ("D2", "B4"),
        ("D3", "B1"), ("D3", "B4"), ("D4", "B3"), ("D4", "B4"),
        ("B1", "C2"), ("B1", "C4"), ("B2", "C1"), ("B2", "C4"),
        ("B3", "C2"), ("B4", "C3"), ("B5", "C1"), ("B5", "C3"),
        ("D1", "D3"), ("D2", "D3"), ("D3", "D4"),
        ("B1", "B2"), ("B1", "B3"), ("B2", "B3"), ("B2", "B4"), ("B3", "B4"),
        ("B4", "B5"), ("B1", "B5"),
        ("C1", "C3"), ("C2", "C3"), ("C1", "C4"), ("C2", "C4"),
    }  # fmt: skip
    assert _edge_set(catalog, "be") == {frozenset((f"be.{a}", f"be.{b}")) for a, b in expected}


def test_fe_edges_match_plan(catalog: SkillsCatalog) -> None:
    expected = {
        ("D1", "B1"), ("D1", "B2"), ("D2", "B2"), ("D3", "B1"), ("D3", "B3"),
        ("D4", "B2"), ("D4", "B5"),
        ("B1", "C3"), ("B1", "C4"), ("B2", "C2"), ("B3", "C3"), ("B4", "C1"),
        ("B5", "C2"), ("B5", "C1"),
        ("D1", "D3"), ("D2", "D3"), ("D3", "D4"),
        ("B1", "B2"), ("B1", "B3"), ("B2", "B4"), ("B3", "B4"), ("B4", "B5"), ("B2", "B5"),
        ("C1", "C2"), ("C2", "C3"), ("C1", "C4"), ("C3", "C4"),
    }  # fmt: skip
    assert _edge_set(catalog, "fe") == {frozenset((f"fe.{a}", f"fe.{b}")) for a, b in expected}


@pytest.mark.parametrize("spec", ["be", "fe", "data"])
def test_every_node_in_at_least_one_edge_and_graph_connected(
    catalog: SkillsCatalog, spec: str
) -> None:
    graph = catalog.spec(spec)
    for node_id, neighbours in graph.adjacency.items():
        assert neighbours, f"{node_id} без рёбер"
    assert is_connected(graph.nodes_by_id, graph.adjacency)
    assert q_level(graph.nodes_by_id, graph.adjacency) == 12


@pytest.mark.parametrize("spec", ["be", "fe", "data"])
def test_edge_kinds_consistent_with_layers(catalog: SkillsCatalog, spec: str) -> None:
    graph = catalog.spec(spec)
    for edge in graph.file.edges:
        src, dst = graph.layer_of(edge.source), graph.layer_of(edge.target)
        if edge.kind == "lateral":
            assert src == dst
        else:
            assert {src, dst} in ({"D", "B"}, {"B", "C"})


def test_bridges_between_be_and_fe(catalog: SkillsCatalog) -> None:
    expected = {
        frozenset(("be.B1", "fe.C2")),
        frozenset(("be.C1", "fe.C1")),
        frozenset(("be.B5", "fe.B5")),
        frozenset(("be.C4", "fe.C4")),
    }
    be_fe = {
        b.key for b in catalog.bridges_for("be") if {b.source[:2], b.target[:2]} == {"be", "fe"}
    }
    assert be_fe == expected
    assert all(b.kind == "bridge" for b in catalog.bridges)
    assert "fe.C2" in catalog.bridge_adjacency["be.B1"]
    assert "be.B1" in catalog.bridge_adjacency["fe.C2"]
    # мосты, объявленные в be/graph.yaml, видны и со стороны fe
    assert expected <= {b.key for b in catalog.bridges_for("fe")}


def test_data_bridges_point_to_existing_nodes(catalog: SkillsCatalog) -> None:
    data_bridges = catalog.bridges_for("data")
    assert data_bridges
    for bridge in data_bridges:
        assert catalog.has_node(bridge.source) and catalog.has_node(bridge.target)
        assert bridge.source.startswith("data.")


def test_decay_parameters_match_plan(catalog: SkillsCatalog) -> None:
    for spec in catalog.specializations.values():
        decay = spec.file.decay
        assert decay.half_life_days == {"test": 240, "fsp": 365, "regular": 120}
        assert decay.layer_factor == {"D": 1.5, "B": 1.0, "C": 0.75}
        assert spec.file.weights.steps("test") == [(0.85, 1.0), (0.7, 0.85), (0.5, 0.45)]
        assert spec.file.weights.steps("regular") == [(0.7, 0.6), (0.5, 0.3)]
        assert spec.file.thresholds.active == 0.5


# --- профили грейдов и демо-подграфы ---------------------------------------------------------


@pytest.mark.parametrize("spec", ["be", "fe"])
def test_grade_profiles_follow_plan_table(catalog: SkillsCatalog, spec: str) -> None:
    profiles = {p.grade: p for p in catalog.profiles(spec)}
    assert list(profiles) == ["junior", "middle", "senior"]

    def ids(*codes: str) -> set[str]:
        return {f"{spec}.{code}" for code in codes}

    junior, middle, senior = profiles["junior"], profiles["middle"], profiles["senior"]
    assert set(junior.target_nodes) == ids("B1", "B2", "B3", "D1")
    assert (junior.task_level, junior.min_active, junior.min_q) == (1, 3, 2)
    assert junior.constraints.as_dict() == {}
    assert set(middle.target_nodes) == ids("B1", "B2", "B3", "B4", "D1", "D4", "C1")
    assert (middle.task_level, middle.min_active, middle.min_q) == (2, 5, 4)
    assert middle.constraints.as_dict() == {"outside_layer": {"layer": "B", "min_active": 1}}
    assert set(senior.target_nodes) == ids(
        "B1", "B2", "B3", "B4", "B5", "D3", "D4", "C1", "C2", "C3"
    )
    assert (senior.task_level, senior.min_active, senior.min_q) == (3, 7, 6)
    assert senior.constraints.as_dict() == {"layers_min_active": {"D": 1, "C": 1}}

    budgets = {g: (p.budget.min_items, p.budget.max_items) for g, p in profiles.items()}
    assert budgets == {"junior": (4, 8), "middle": (7, 11), "senior": (10, 12)}


@pytest.mark.parametrize("spec", ["be", "fe"])
def test_demo_profiles_are_connected_subsets_and_passable(
    catalog: SkillsCatalog, spec: str
) -> None:
    graph = catalog.spec(spec)
    expected_sizes = {"junior": 3, "middle": 4, "senior": 5}
    expected_thresholds = {"junior": (2, 1), "middle": (3, 2), "senior": (4, 3)}
    for profile in catalog.profiles(spec):
        demo = profile.demo
        assert len(demo.target_nodes) == expected_sizes[profile.grade]
        assert (demo.min_active, demo.min_q) == expected_thresholds[profile.grade]
        assert set(demo.target_nodes) <= set(profile.target_nodes)
        assert is_connected(demo.target_nodes, graph.adjacency)
        # те же структурные условия, что у полного профиля
        assert demo.constraints == profile.constraints
        # проходим при верных ответах на все задания
        check = check_grade_profile(
            demo.requirements(), demo.target_nodes, graph.adjacency, graph.layer_of
        )
        assert check.passed and not check.missing_nodes
        assert check.q == len(demo.target_nodes) - 1


def test_be_demo_subgraphs_match_plan(catalog: SkillsCatalog) -> None:
    demos = {p.grade: set(p.demo.target_nodes) for p in catalog.profiles("be")}
    assert demos["junior"] == {"be.B1", "be.B2", "be.D1"}
    assert demos["middle"] == {"be.B1", "be.B2", "be.D1", "be.C1"}
    assert demos["senior"] == {"be.B1", "be.B4", "be.D4", "be.C2", "be.C3"}


@pytest.mark.parametrize("spec", ["be", "fe"])
def test_full_target_subgraphs_connected_and_passable(catalog: SkillsCatalog, spec: str) -> None:
    graph = catalog.spec(spec)
    for profile in catalog.profiles(spec):
        assert is_connected(profile.target_nodes, graph.adjacency)
        check = check_grade_profile(
            profile.requirements(), profile.target_nodes, graph.adjacency, graph.layer_of
        )
        assert check.passed, (profile.grade, check.failed_checks)


def test_preview_specialization_has_no_profiles(catalog: SkillsCatalog) -> None:
    assert catalog.profiles("data") == ()


# --- словари ---------------------------------------------------------------------------------


def test_industries_from_plan(catalog: SkillsCatalog) -> None:
    codes = [item.code for item in catalog.industries]
    assert len(codes) == 8 and len(set(codes)) == 8
    assert "it_products" in codes and "other" in codes


def test_keywords_cover_every_node_with_at_least_8_words(catalog: SkillsCatalog) -> None:
    words = catalog.keywords.nodes
    for node in catalog.iter_nodes():
        assert len(words[node.id]) >= 8, node.id
        assert all(w == w.lower() and w.strip() == w for w in words[node.id]), node.id
    assert set(catalog.keywords.specializations) == {"be", "fe", "data"}
    unsupported = {item.code: item for item in catalog.keywords.unsupported_specializations}
    assert {"mobile", "ml"} <= set(unsupported)
    assert unsupported["mobile"].nearest == "fe"
    for item in unsupported.values():
        assert item.nearest in catalog.specializations


def test_fsp_mapping_references_existing_nodes(catalog: SkillsCatalog) -> None:
    mapping = catalog.fsp_mapping
    assert mapping.half_life_days == 365
    assert mapping.tier_weights.model_dump() == {
        "winner": 1.0,
        "prize": 0.8,
        "finalist": 0.6,
        "participant": 0.35,
    }
    assert mapping.team_bonus.node_code == "C4" and mapping.team_bonus.weight == 0.5
    assert mapping.participation_only.w0 == 0.2
    codes = {d.code for d in mapping.disciplines}
    assert {"algorithmic", "hackathon_backend", "hackathon_frontend", "ctf", "product"} <= codes
    hackathon_be = next(d for d in mapping.disciplines if d.code == "hackathon_backend")
    assert {ref.id for ref in hackathon_be.nodes} == {"be.B1", "be.B2", "be.C1", "be.C4"}
    algorithmic = next(d for d in mapping.disciplines if d.code == "algorithmic")
    assert [(ref.code, ref.weight) for ref in algorithmic.nodes] == [("D2", 1.0), ("D1", 0.6)]
    assert algorithmic.level.by == "percentile"


# --- загрузчик: кэш и отказ при повреждённом YAML --------------------------------------------


def test_get_catalog_uses_settings_path_and_cache() -> None:
    reset_catalog_cache()
    first = get_catalog()
    assert first.source_dir == get_settings().skills_path.resolve()
    assert get_catalog() is first


def _copy_catalog(tmp_path: Path) -> Path:
    target = tmp_path / "skills"
    shutil.copytree(
        SKILLS_DIR, target, ignore=shutil.ignore_patterns("templates", "__pycache__", "*.py")
    )
    return target


def test_loader_rejects_edge_to_unknown_node(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    graph_path = root / "be" / "graph.yaml"
    data = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    data["edges"].append({"source": "be.D1", "target": "be.B9", "kind": "vertical"})
    graph_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SkillsCatalogError, match="неизвестный узел"):
        load_catalog(root)


def test_loader_rejects_lonely_node(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    graph_path = root / "fe" / "graph.yaml"
    data = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    data["edges"] = [e for e in data["edges"] if "fe.C4" not in (e["source"], e["target"])]
    graph_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SkillsCatalogError, match="узлы без рёбер: fe.C4"):
        load_catalog(root)


def test_loader_rejects_disconnected_demo_profile(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    profiles_path = root / "grade_profiles.yaml"
    data = yaml.safe_load(profiles_path.read_text(encoding="utf-8"))
    junior = next(p for p in data["specializations"]["be"] if p["grade"] == "junior")
    junior["demo"]["target_nodes"] = ["be.B3", "be.D1"]  # D1–B3 ребра нет
    profiles_path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    with pytest.raises(SkillsCatalogError, match="demo.*не связен"):
        load_catalog(root)


def test_loader_rejects_unknown_field(tmp_path: Path) -> None:
    root = _copy_catalog(tmp_path)
    industries_path = root / "industries.yaml"
    industries_path.write_text(
        "version: 1\nindustries:\n  - {code: fintech, title: Финтех, colour: red}\n",
        encoding="utf-8",
    )
    with pytest.raises(SkillsCatalogError, match="industries.yaml"):
        load_catalog(root)
