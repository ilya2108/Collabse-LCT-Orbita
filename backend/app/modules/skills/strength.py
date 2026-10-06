"""Чистые функции модели компетенций (PLAN.md §3.3–3.6): сила узла, уровень Q, грейд, форма.

Модуль не зависит от БД, настроек и загрузчика YAML: на вход — подтверждения, множества
активных узлов и смежность графа, на выход — числа и неизменяемые результаты. Все
числовые параметры (полураспады, пороги) приходят извне (graph.yaml через loader);
константы ниже — только значения по умолчанию, совпадающие с планом.

Идентификатор узла имеет вид `<spec>.<Layer><n>` («be.D1»), поэтому специализация и слой
узла извлекаются из id без обращения к справочнику (`spec_of`, `layer_of`).

Контракт со строками БД (`models.candidate`): `Confirmation.from_row` приводит Numeric
`w0` к float, `NodeState.strength_by_level_json` отдаёт JSONB-ключи строками,
формы профиля `dot | I | T | P` совпадают с CHECK `candidate_category.shape`
(`none` — категория не записывается).

Теоретическая основа — Квантовая модель компетенций К. Кичинского (2023, открытая
публикация, spectralthinking.org); формулы и применение к подбору — разработка команды.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol

# --- константы по умолчанию (источник правды — graph.yaml) ------------------------------

ACTIVE_THRESHOLD = 0.5  # узел активен при S_v ≥ 0.5
FRESH_THRESHOLD = 0.8  # свежо: w_now ≥ 0.8
DIM_THRESHOLD = 0.5  # тускнеет: 0.5 ≤ w_now < 0.8; погасло: w_now < 0.5
LEVELS: tuple[int, ...] = (1, 2, 3)
LAYERS: tuple[str, ...] = ("D", "B", "C")
STRONG_PASS_MEAN_SCORE = 0.85  # «справился уверенно»: все узлы T активны и средний балл ≥ 0.85

Freshness = Literal["fresh", "dim", "faded"]
FRESHNESS_TITLES: dict[str, str] = {"fresh": "свежо", "dim": "тускнеет", "faded": "погасло"}

# none — активных узлов своей специализации нет (категория не пишется);
# dot | I | T | P — значения CHECK-ограничения candidate_category.shape
Shape = Literal["none", "dot", "I", "T", "P"]
PERSISTED_SHAPES: tuple[str, ...] = ("dot", "I", "T", "P")
SHAPE_TITLES: dict[str, str] = {"none": "—", "dot": "точка", "I": "I", "T": "T", "P": "П"}

Adjacency = Mapping[str, AbstractSet[str]]


def spec_of(node_id: str) -> str:
    """Специализация по id узла: «be.D1» → «be»."""
    return node_id.split(".", 1)[0]


def layer_of(node_id: str) -> str:
    """Слой по id узла: «be.D1» → «D»."""
    return node_id.split(".", 1)[1][0]


# --- подтверждения и сила узла (§3.3) ----------------------------------------------------


class ConfirmationRow(Protocol):
    """Минимальный интерфейс строки `skill_confirmation` (ORM-модель или любой объект)."""

    node_id: str
    level: int
    w0: Any  # Numeric(5,4) → Decimal в ORM
    confirmed_at: datetime
    half_life_days: Any
    source: str


@dataclass(frozen=True)
class Confirmation:
    """Одно подтверждение узла (строка `skill_confirmation`)."""

    node_id: str
    level: int  # уровень подтверждения L1..L3
    w0: float  # базовый вес по источнику
    confirmed_at: datetime
    half_life_days: float  # τ = τ_source × k_layer, уже рассчитанный при записи
    source: str = "test"  # test | fsp | regular

    @classmethod
    def from_row(cls, row: ConfirmationRow) -> Confirmation:
        """Адаптер строки БД: Decimal `w0`/`half_life_days` → float (Decimal × float —
        TypeError), остальное как есть."""
        return cls(
            node_id=row.node_id,
            level=int(row.level),
            w0=float(row.w0),
            confirmed_at=row.confirmed_at,
            half_life_days=float(row.half_life_days),
            source=row.source,
        )


def decay_factor(confirmed_at: datetime, now: datetime, half_life_days: float) -> float:
    """Множитель затухания 2^(−Δt/τ); подтверждения «из будущего» не усиливаются."""
    age_days = max((now - confirmed_at).total_seconds() / 86_400, 0.0)
    return 2.0 ** (-age_days / half_life_days)


def current_weight(confirmation: Confirmation, now: datetime) -> float:
    """Текущий вес подтверждения w0 · 2^(−Δt/τ)."""
    return confirmation.w0 * decay_factor(
        confirmation.confirmed_at, now, confirmation.half_life_days
    )


def node_strength(confirmations: Iterable[Confirmation], level: int, now: datetime) -> float:
    """S_v(L, t) = 1 − Π(1 − w_e(t)) по подтверждениям уровня ≥ L (noisy-OR).

    Одно слабое подтверждение (0.45) узел не активирует; два слабых (0.45 и 0.3) дают
    1 − 0.55·0.7 = 0.615 — «реанимация памяти усилием».
    """
    survive = 1.0
    for confirmation in confirmations:
        if confirmation.level >= level:
            survive *= 1.0 - min(max(current_weight(confirmation, now), 0.0), 1.0)
    return 1.0 - survive


def is_active(
    confirmations: Iterable[Confirmation],
    level: int,
    now: datetime,
    threshold: float = ACTIVE_THRESHOLD,
) -> bool:
    """Узел активен на уровне L, если S_v(L, t) ≥ порога (по умолчанию 0.5)."""
    return node_strength(confirmations, level, now) >= threshold


def strength_by_level(
    confirmations: Iterable[Confirmation], now: datetime, levels: Sequence[int] = LEVELS
) -> dict[int, float]:
    """Сила узла по каждому уровню: {1: S(1), 2: S(2), 3: S(3)}."""
    items = list(confirmations)
    return {level: node_strength(items, level, now) for level in levels}


def active_level(
    confirmations: Iterable[Confirmation],
    now: datetime,
    threshold: float = ACTIVE_THRESHOLD,
    levels: Sequence[int] = LEVELS,
) -> int:
    """Максимальный уровень, на котором узел активен; 0 — узел не активен."""
    items = list(confirmations)
    best = 0
    for level in levels:
        if node_strength(items, level, now) >= threshold:
            best = max(best, level)
    return best


def w_now(confirmations: Iterable[Confirmation], now: datetime) -> float:
    """Свежесть узла: максимальный текущий вес одного подтверждения."""
    return max((current_weight(c, now) for c in confirmations), default=0.0)


def freshness(
    weight_now: float, fresh: float = FRESH_THRESHOLD, dim: float = DIM_THRESHOLD
) -> Freshness:
    """Состояние свежести для UI: свежо / тускнеет / погасло."""
    if weight_now >= fresh:
        return "fresh"
    if weight_now >= dim:
        return "dim"
    return "faded"


def freshest_at(confirmations: Iterable[Confirmation]) -> datetime | None:
    """Дата самого свежего подтверждения."""
    return max((c.confirmed_at for c in confirmations), default=None)


def half_life_for(
    source: str, layer: str, half_life_days: Mapping[str, float], layer_factor: Mapping[str, float]
) -> float:
    """τ = τ_source × k_layer (числа — из graph.yaml: decay.half_life_days, decay.layer_factor)."""
    return float(half_life_days[source]) * float(layer_factor[layer])


def w0_for_score(score: float, steps: Sequence[tuple[float, float]]) -> float | None:
    """Базовый вес по баллу: ступени (min_score, w0) по убыванию порога; None — подтверждения
    нет (балл ниже последней ступени; для теста < 0.5 балл остаётся только в сессии)."""
    for min_score, w0 in sorted(steps, key=lambda step: step[0], reverse=True):
        if score >= min_score:
            return w0
    return None


@dataclass(frozen=True)
class NodeState:
    """Снимок `candidate_skill_state` для одного узла."""

    node_id: str
    active_level: int
    strength_by_level: dict[int, float]
    w_now: float
    freshness: Freshness
    freshest_at: datetime | None

    @property
    def strength_by_level_json(self) -> dict[str, float]:
        """Форма JSONB-колонки `candidate_skill_state.strength_by_level`: ключи — строки
        («{"1": 0.93, "2": 0.61, "3": 0.0}»), значения округлены до 4 знаков."""
        return {str(level): round(value, 4) for level, value in self.strength_by_level.items()}


def node_state(
    node_id: str,
    confirmations: Iterable[Confirmation],
    now: datetime,
    threshold: float = ACTIVE_THRESHOLD,
    fresh: float = FRESH_THRESHOLD,
    dim: float = DIM_THRESHOLD,
) -> NodeState:
    """Материализованное состояние узла по всем его подтверждениям."""
    items = [c for c in confirmations if c.node_id == node_id]
    weight_now = w_now(items, now)
    return NodeState(
        node_id=node_id,
        active_level=active_level(items, now, threshold),
        strength_by_level=strength_by_level(items, now),
        w_now=weight_now,
        freshness=freshness(weight_now, fresh, dim),
        freshest_at=freshest_at(items),
    )


# --- граф: компоненты и уровень Q (§3.4) -------------------------------------------------


def components(nodes: Iterable[str], adjacency: Adjacency) -> list[frozenset[str]]:
    """Компоненты связности подграфа, порождённого `nodes` (рёбра — из `adjacency`)."""
    remaining = set(nodes)
    result: list[frozenset[str]] = []
    while remaining:
        start = min(remaining)  # детерминированный порядок обхода
        seen = {start}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbour in adjacency.get(current, ()):
                if neighbour in remaining and neighbour not in seen:
                    seen.add(neighbour)
                    queue.append(neighbour)
        result.append(frozenset(seen))
        remaining -= seen
    return result


def is_connected(nodes: Iterable[str], adjacency: Adjacency) -> bool:
    """Связен ли порождённый подграф (пустое множество считается связным)."""
    return len(components(nodes, adjacency)) <= 1


def q_level(active_nodes: Iterable[str], adjacency: Adjacency) -> int:
    """Q = |A| − C(A): размер остовного леса по активным узлам.

    Один узел → 0, цепочка из четырёх → 3, две несвязанные пары → 2.
    """
    nodes = set(active_nodes)
    if not nodes:
        return 0
    return len(nodes) - len(components(nodes, adjacency))


# --- профиль грейда (§3.4, §4.3) ---------------------------------------------------------


@dataclass(frozen=True)
class GradeRequirements:
    """Условия профиля грейда на целевом подграфе T (полный или демо-профиль)."""

    target_nodes: tuple[str, ...]
    min_active: int
    min_q: int
    # «вышел за базу»: активно ≥ n узлов вне слоя (layer, n)
    outside_layer: tuple[str, int] | None = None
    # «≥ 1 узел D и ≥ 1 узел C»: минимум активных узлов по слоям
    layers_min_active: Mapping[str, int] = field(default_factory=dict)
    # «ходок»: активны узлы всех трёх слоёв среди T
    walker: bool = False


@dataclass(frozen=True)
class GradeCheck:
    """Результат проверки профиля: что активно, чего не хватило, какие условия не выполнены."""

    passed: bool
    active_nodes: tuple[str, ...]  # активные узлы T
    missing_nodes: tuple[str, ...]  # «не хватило узлов N1, N2»
    q: int
    # коды невыполненных условий: min_active | min_q | outside_layer | layers_min_active | walker
    failed_checks: tuple[str, ...]


def check_grade_profile(
    requirements: GradeRequirements,
    active_nodes: Iterable[str],
    adjacency: Adjacency,
    layer_of_node: Callable[[str], str] = layer_of,
    layers: Sequence[str] = LAYERS,
) -> GradeCheck:
    """Проверка порога грейда по активным узлам текущей сессии на целевом подграфе."""
    target = tuple(requirements.target_nodes)
    active_target = {node for node in active_nodes if node in target}
    q = q_level(active_target, adjacency)
    failed: list[str] = []

    if len(active_target) < requirements.min_active:
        failed.append("min_active")
    if q < requirements.min_q:
        failed.append("min_q")

    active_layers = [layer_of_node(node) for node in active_target]
    if requirements.outside_layer is not None:
        layer, minimum = requirements.outside_layer
        if sum(1 for item in active_layers if item != layer) < minimum:
            failed.append("outside_layer")
    for layer, minimum in requirements.layers_min_active.items():
        if active_layers.count(layer) < minimum:
            failed.append("layers_min_active")
            break
    if requirements.walker and not set(layers) <= set(active_layers):
        failed.append("walker")

    return GradeCheck(
        passed=not failed,
        active_nodes=tuple(node for node in target if node in active_target),
        missing_nodes=tuple(node for node in target if node not in active_target),
        q=q,
        failed_checks=tuple(failed),
    )


def is_confident_pass(
    requirements: GradeRequirements,
    active_nodes: Iterable[str],
    node_scores: Mapping[str, float],
    min_mean_score: float = STRONG_PASS_MEAN_SCORE,
) -> bool:
    """«Справился уверенно»: все узлы T активны и средний node_score по T ≥ 0.85 —
    предлагается тест на грейд выше."""
    target = requirements.target_nodes
    if not target or not set(target) <= set(active_nodes):
        return False
    scores = [node_scores.get(node, 0.0) for node in target]
    return sum(scores) / len(scores) >= min_mean_score


# --- форма профиля, ходок, хаб (§3.6) ----------------------------------------------------


@dataclass(frozen=True)
class ProfileShape:
    """Форма профиля и бейджи по активным узлам."""

    shape: Shape
    walker: bool  # ходок: активны узлы всех трёх слоёв своей специализации
    hub: bool  # хаб: активные мосты в ≥ 2 другие специализации
    home_layers: tuple[str, ...]  # активные слои своей специализации
    bridged_specs: tuple[str, ...]  # специализации, в которые ведут активные мосты


def profile_shape(
    active_nodes: Iterable[str],
    home_spec: str,
    bridge_adjacency: Adjacency,
    spec_of_node: Callable[[str], str] = spec_of,
    layer_of_node: Callable[[str], str] = layer_of,
    layers: Sequence[str] = LAYERS,
) -> ProfileShape:
    """Детерминированная форма по активным узлам:

    - none — активных узлов своей специализации нет (категория не записывается);
    - dot (точка) — активные узлы своей специализации лежат в одном слое (в т. ч. один узел);
    - I — только своя специализация, активны ≥ 2 слоя;
    - T — I плюс активный мост (оба конца активны) в другую специализацию;
    - П — T плюс в соседней специализации активны узлы ≥ 2 слоёв.

    Мост считается активным, когда активны оба его конца — иначе чужой узел не связан
    с профилем и форму не меняет.
    """
    active = set(active_nodes)
    home = {node for node in active if spec_of_node(node) == home_spec}
    home_layers = tuple(layer for layer in layers if any(layer_of_node(n) == layer for n in home))
    walker = len(home_layers) == len(layers)

    bridged: dict[str, set[str]] = {}
    for node in home:
        for other in bridge_adjacency.get(node, ()):
            if other in active and spec_of_node(other) != home_spec:
                bridged.setdefault(spec_of_node(other), set()).add(other)
    bridged_specs = tuple(sorted(bridged))
    hub = len(bridged_specs) >= 2

    if not home:
        shape: Shape = "none"
    elif len(home_layers) < 2:
        shape = "dot"
    elif not bridged_specs:
        shape = "I"
    else:
        shape = "T"
        for spec in bridged_specs:
            foreign_active = {node for node in active if spec_of_node(node) == spec}
            foreign_layers = {layer_of_node(node) for node in foreign_active}
            if len(foreign_layers) >= 2:
                shape = "P"
                break

    return ProfileShape(
        shape=shape,
        walker=walker,
        hub=hub,
        home_layers=home_layers,
        bridged_specs=bridged_specs,
    )
