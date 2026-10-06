"""Чистые функции модели компетенций (PLAN.md §3.3–3.6): числовые примеры из плана,
уровень Q, профили грейдов на синтетических наборах, формы профиля и бейджи."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import get_args

import pytest

from app.modules.skills import strength as s

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
DECAY_HALF_LIFE = {"test": 240.0, "fsp": 365.0, "regular": 120.0}
DECAY_LAYER = {"D": 1.5, "B": 1.0, "C": 0.75}


def conf(
    w0: float,
    *,
    node: str = "be.B1",
    level: int = 1,
    days_ago: float = 0,
    half_life: float = 240.0,
    source: str = "test",
) -> s.Confirmation:
    return s.Confirmation(
        node_id=node,
        level=level,
        w0=w0,
        confirmed_at=NOW - timedelta(days=days_ago),
        half_life_days=half_life,
        source=source,
    )


# --- сила узла (§3.3) -------------------------------------------------------------------------


def test_single_weak_confirmation_does_not_activate() -> None:
    items = [conf(0.45)]
    assert s.node_strength(items, 1, NOW) == pytest.approx(0.45)
    assert not s.is_active(items, 1, NOW)


def test_two_weak_confirmations_reanimate_node() -> None:
    # старый тест 0.45 + свежее регулярное задание 0.3 → 1 − 0.55·0.7 = 0.615
    items = [conf(0.45), conf(0.3, source="regular", half_life=120.0)]
    assert s.node_strength(items, 1, NOW) == pytest.approx(0.615)
    assert s.is_active(items, 1, NOW)


def test_decay_halves_weight_after_half_life() -> None:
    assert s.decay_factor(NOW - timedelta(days=240), NOW, 240.0) == pytest.approx(0.5)
    assert s.decay_factor(NOW - timedelta(days=480), NOW, 240.0) == pytest.approx(0.25)
    assert s.decay_factor(NOW, NOW, 240.0) == pytest.approx(1.0)
    # подтверждение «из будущего» не усиливается
    assert s.decay_factor(NOW + timedelta(days=10), NOW, 240.0) == pytest.approx(1.0)


def test_strong_test_keeps_node_active_for_half_life_on_layer_b() -> None:
    assert s.is_active([conf(1.0, days_ago=240)], 1, NOW)  # ровно 0.5 — ещё активен
    assert not s.is_active([conf(1.0, days_ago=241)], 1, NOW)
    # слой D держит дольше (τ = 240 × 1.5 = 360)
    assert s.is_active([conf(1.0, days_ago=300, half_life=360.0)], 1, NOW)


def test_strength_respects_confirmation_level() -> None:
    items = [conf(1.0, level=1), conf(0.6, level=2)]
    by_level = s.strength_by_level(items, NOW)
    assert by_level[1] == pytest.approx(1.0)
    assert by_level[2] == pytest.approx(0.6)
    assert by_level[3] == pytest.approx(0.0)
    assert s.active_level(items, NOW) == 2
    assert s.active_level([conf(0.45)], NOW) == 0


def test_w_now_and_freshness_states() -> None:
    assert s.freshness(s.w_now([conf(1.0), conf(0.3)], NOW)) == "fresh"
    assert s.freshness(s.w_now([conf(0.85, days_ago=120)], NOW)) == "dim"  # 0.85·0.707 ≈ 0.6
    assert s.freshness(s.w_now([conf(0.45)], NOW)) == "faded"
    assert s.w_now([], NOW) == 0.0
    assert s.freshness(0.0) == "faded"


def test_node_state_snapshot() -> None:
    items = [conf(1.0, days_ago=30), conf(0.3, node="be.B2")]
    state = s.node_state("be.B1", items, NOW)
    assert state.node_id == "be.B1"
    assert state.active_level == 1
    assert state.freshness == "fresh"
    assert state.freshest_at == NOW - timedelta(days=30)
    assert s.node_state("be.C9", items, NOW).active_level == 0


def test_half_life_for_source_and_layer() -> None:
    assert s.half_life_for("test", "B", DECAY_HALF_LIFE, DECAY_LAYER) == 240
    assert s.half_life_for("test", "D", DECAY_HALF_LIFE, DECAY_LAYER) == 360
    assert s.half_life_for("fsp", "D", DECAY_HALF_LIFE, DECAY_LAYER) == 547.5
    assert s.half_life_for("regular", "C", DECAY_HALF_LIFE, DECAY_LAYER) == 90


def test_w0_for_score_steps() -> None:
    test_steps = [(0.85, 1.0), (0.7, 0.85), (0.5, 0.45)]
    assert s.w0_for_score(0.9, test_steps) == 1.0
    assert s.w0_for_score(0.85, test_steps) == 1.0
    assert s.w0_for_score(0.75, test_steps) == 0.85
    assert s.w0_for_score(0.5, test_steps) == 0.45
    assert s.w0_for_score(0.49, test_steps) is None
    regular_steps = [(0.7, 0.6), (0.5, 0.3)]
    assert s.w0_for_score(0.7, regular_steps) == 0.6
    assert s.w0_for_score(0.55, regular_steps) == 0.3


# --- уровень Q (§3.4) ---------------------------------------------------------------------------

CHAIN = {"a": {"b"}, "b": {"a", "c"}, "c": {"b", "d"}, "d": {"c"}}
TWO_PAIRS = {"a": {"b"}, "b": {"a"}, "c": {"d"}, "d": {"c"}}


def test_q_level_examples_from_plan() -> None:
    assert s.q_level(["a"], CHAIN) == 0
    assert s.q_level(["a", "b", "c", "d"], CHAIN) == 3
    assert s.q_level(["a", "b", "c", "d"], TWO_PAIRS) == 2
    assert s.q_level([], CHAIN) == 0
    # узлы, не входящие в активное множество, не связывают: a и c без b — две компоненты
    assert s.q_level(["a", "c"], CHAIN) == 0


def test_components_deterministic() -> None:
    assert s.components(["d", "a", "c"], CHAIN) == [frozenset({"a"}), frozenset({"c", "d"})]
    assert s.is_connected([], CHAIN)
    assert not s.is_connected(["a", "d"], CHAIN)


# --- профиль грейда (§3.4) ---------------------------------------------------------------------

# синтетический граф в терминах be: D1–B1, D1–B2, B1–B2, B1–B3, B2–B3, B2–C1, D4–B3, D4–B4, B4–C3
ADJ = {
    "x.D1": {"x.B1", "x.B2"},
    "x.B1": {"x.D1", "x.B2", "x.B3"},
    "x.B2": {"x.D1", "x.B1", "x.B3", "x.C1"},
    "x.B3": {"x.B1", "x.B2", "x.D4"},
    "x.C1": {"x.B2"},
    "x.D4": {"x.B3", "x.B4"},
    "x.B4": {"x.D4", "x.C3"},
    "x.C3": {"x.B4"},
}

JUNIOR = s.GradeRequirements(("x.B1", "x.B2", "x.B3", "x.D1"), min_active=3, min_q=2)
MIDDLE = s.GradeRequirements(
    ("x.B1", "x.B2", "x.B3", "x.D1", "x.C1"),
    min_active=3,
    min_q=2,
    outside_layer=("B", 1),
)
SENIOR = s.GradeRequirements(
    ("x.B1", "x.B2", "x.B3", "x.B4", "x.D1", "x.D4", "x.C1", "x.C3"),
    min_active=4,
    min_q=3,
    layers_min_active={"D": 1, "C": 1},
)


def test_junior_passes_with_three_connected_nodes() -> None:
    check = s.check_grade_profile(JUNIOR, ["x.B1", "x.B2", "x.D1", "x.C9"], ADJ)
    assert check.passed
    assert check.active_nodes == ("x.B1", "x.B2", "x.D1")
    assert check.missing_nodes == ("x.B3",)
    assert check.q == 2 and check.failed_checks == ()


def test_junior_fails_on_count_or_connectivity() -> None:
    too_few = s.check_grade_profile(JUNIOR, ["x.B1", "x.B2"], ADJ)
    assert not too_few.passed and too_few.failed_checks == ("min_active", "min_q")
    assert too_few.missing_nodes == ("x.B3", "x.D1")
    # три активных узла, но B3 и D1 не соединены напрямую: Q = 1 < 2
    adjacency = {"x.D1": {"x.B1"}, "x.B1": {"x.D1"}, "x.B3": set()}
    split = s.check_grade_profile(JUNIOR, ["x.B1", "x.B3", "x.D1"], adjacency)
    assert not split.passed and split.failed_checks == ("min_q",)


def test_middle_requires_node_outside_base_layer() -> None:
    only_base = s.check_grade_profile(MIDDLE, ["x.B1", "x.B2", "x.B3"], ADJ)
    assert not only_base.passed and only_base.failed_checks == ("outside_layer",)
    with_context = s.check_grade_profile(MIDDLE, ["x.B1", "x.B2", "x.C1"], ADJ)
    assert with_context.passed and with_context.q == 2


def test_senior_requires_depth_and_context() -> None:
    no_depth = s.check_grade_profile(SENIOR, ["x.B1", "x.B2", "x.B3", "x.C1"], ADJ)
    assert not no_depth.passed and no_depth.failed_checks == ("layers_min_active",)
    walker = s.check_grade_profile(SENIOR, ["x.D1", "x.B1", "x.B2", "x.C1"], ADJ)
    assert walker.passed and walker.q == 3


def test_walker_constraint() -> None:
    req = s.GradeRequirements(("x.D1", "x.B1", "x.B2", "x.C1"), 2, 1, walker=True)
    assert not s.check_grade_profile(req, ["x.D1", "x.B1"], ADJ).passed
    assert s.check_grade_profile(req, ["x.D1", "x.B2", "x.C1"], ADJ).passed


def test_confident_pass_needs_all_targets_and_high_mean() -> None:
    scores = {"x.B1": 0.9, "x.B2": 0.9, "x.B3": 0.8, "x.D1": 0.85}
    assert s.is_confident_pass(JUNIOR, JUNIOR.target_nodes, scores)
    assert not s.is_confident_pass(JUNIOR, ["x.B1", "x.B2", "x.D1"], scores)
    assert not s.is_confident_pass(JUNIOR, JUNIOR.target_nodes, {**scores, "x.B3": 0.5})


# --- форма профиля, ходок, хаб (§3.6) ---------------------------------------------------------

BRIDGES = {
    "be.B1": {"fe.C2"},
    "fe.C2": {"be.B1"},
    "be.C4": {"fe.C4", "data.C4"},
    "fe.C4": {"be.C4"},
    "data.C4": {"be.C4"},
}


def test_shape_none_and_dot() -> None:
    assert s.profile_shape([], "be", BRIDGES).shape == "none"
    assert s.profile_shape(["fe.B1"], "be", BRIDGES).shape == "none"
    dot = s.profile_shape(["be.B1"], "be", BRIDGES)
    assert dot.shape == "dot" and dot.home_layers == ("B",)
    assert s.profile_shape(["be.B1", "be.B2", "be.B3"], "be", BRIDGES).shape == "dot"


def test_shape_i_and_walker() -> None:
    shape = s.profile_shape(["be.D1", "be.B1"], "be", BRIDGES)
    assert shape.shape == "I" and not shape.walker and shape.bridged_specs == ()
    walker = s.profile_shape(["be.D1", "be.B1", "be.C1"], "be", BRIDGES)
    assert walker.shape == "I" and walker.walker and walker.home_layers == ("D", "B", "C")


def test_shape_t_requires_active_bridge_on_both_ends() -> None:
    # чужой узел без активного моста форму не меняет
    assert s.profile_shape(["be.D1", "be.B1", "fe.B3"], "be", BRIDGES).shape == "I"
    t_shape = s.profile_shape(["be.D1", "be.B1", "fe.C2"], "be", BRIDGES)
    assert t_shape.shape == "T" and t_shape.bridged_specs == ("fe",)


def test_shape_p_when_neighbour_has_two_layers() -> None:
    p_shape = s.profile_shape(["be.D1", "be.B1", "fe.C2", "fe.B2"], "be", BRIDGES)
    assert p_shape.shape == "P" and not p_shape.hub


def test_hub_requires_bridges_into_two_specializations() -> None:
    hub = s.profile_shape(["be.B1", "be.C4", "fe.C4", "data.C4"], "be", BRIDGES)
    assert hub.hub and hub.bridged_specs == ("data", "fe") and hub.shape == "T"


def test_shape_titles_are_russian() -> None:
    assert s.SHAPE_TITLES["P"] == "П" and s.SHAPE_TITLES["dot"] == "точка"
    assert s.FRESHNESS_TITLES == {"fresh": "свежо", "dim": "тускнеет", "faded": "погасло"}


# --- контракт с моделью данных (candidate_category.shape, skill_confirmation.w0) -------------


def test_shapes_match_db_check_constraint() -> None:
    from app.models.candidate import PROFILE_SHAPES

    persisted = set(get_args(s.Shape)) - {"none"}
    assert persisted == set(PROFILE_SHAPES) == set(s.PERSISTED_SHAPES)
    assert set(s.SHAPE_TITLES) == set(get_args(s.Shape))


def test_confirmation_from_db_row_and_json_keys() -> None:
    row = SimpleNamespace(
        node_id="be.B1",
        level=2,
        w0=Decimal("0.8500"),
        confirmed_at=NOW - timedelta(days=30),
        half_life_days=240,
        source="test",
    )
    confirmation = s.Confirmation.from_row(row)
    assert isinstance(confirmation.w0, float) and confirmation.w0 == 0.85
    assert isinstance(confirmation.half_life_days, float)
    assert s.current_weight(confirmation, NOW) == pytest.approx(0.85 * 2 ** (-30 / 240))
    state = s.node_state("be.B1", [confirmation], NOW)
    assert set(state.strength_by_level) == {1, 2, 3}
    assert set(state.strength_by_level_json) == {"1", "2", "3"}
    assert state.strength_by_level_json["3"] == 0.0
