"""Загрузка и валидация справочника навыков из YAML (PLAN.md §3.2–3.6).

Каталог `settings.skills_path` (по умолчанию backend/app/skills):
- `<spec>/graph.yaml` — специализация, слои, узлы, рёбра, мосты, параметры затухания и веса;
- `grade_profiles.yaml` — профили грейдов (полные и демо) по специализациям;
- `fsp_mapping.yaml` — маппинг дисциплин ФСП в узлы;
- `keywords.yaml` — словарь «ключевое слово → узел» для режима rules;
- `industries.yaml` — справочник отраслей.

Каталог читается один раз и кэшируется в памяти процесса (`get_catalog()`). Все файлы
проверяются Pydantic-схемами (лишние поля запрещены — опечатка в YAML не проходит молча)
и структурными инвариантами: id узлов уникальны и соответствуют специализации, рёбра
ссылаются на существующие узлы и согласованы со слоями, каждый узел входит хотя бы в одно
ребро, мосты ведут в другие загруженные специализации, целевые подграфы профилей грейдов
(полные и демо) связны и проходимы при всех активных узлах, ссылки словарей — на
существующие узлы. Подпапки `<spec>/templates/` — зона модуля assessment, здесь не читаются.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import get_settings
from app.modules.skills.strength import GradeRequirements, check_grade_profile, is_connected

Layer = Literal["D", "B", "C"]
EdgeKind = Literal["vertical", "lateral", "bridge"]
SpecStatus = Literal["active", "preview"]
Grade = Literal["junior", "middle", "senior"]
Source = Literal["test", "fsp", "regular"]
Tier = Literal["winner", "prize", "finalist", "participant"]

LAYER_ORDER: tuple[Layer, ...] = ("D", "B", "C")
GRADE_ORDER: tuple[Grade, ...] = ("junior", "middle", "senior")
NODE_ID_RE = re.compile(r"^(?P<spec>[a-z][a-z0-9_]*)\.(?P<code>(?P<layer>[DBC])[1-9][0-9]*)$")

GRAPH_FILE = "graph.yaml"
GRADE_PROFILES_FILE = "grade_profiles.yaml"
FSP_MAPPING_FILE = "fsp_mapping.yaml"
KEYWORDS_FILE = "keywords.yaml"
INDUSTRIES_FILE = "industries.yaml"
TEMPLATES_DIR = "templates"


class SkillsCatalogError(ValueError):
    """Справочник не прошёл валидацию; сообщение перечисляет все найденные проблемы."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- graph.yaml ----------------------------------------------------------------------------


class LayerDef(_Strict):
    code: Layer
    title: str = Field(min_length=1)


class NodeDef(_Strict):
    id: str
    code: str
    layer: Layer
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)


class EdgeDef(_Strict):
    source: str
    target: str
    kind: EdgeKind
    title: str | None = None

    @property
    def key(self) -> frozenset[str]:
        """Ненаправленный ключ ребра для поиска дубликатов."""
        return frozenset((self.source, self.target))


class DecayConfig(_Strict):
    half_life_days: dict[Source, float]
    layer_factor: dict[Layer, float]

    @model_validator(mode="after")
    def _complete_and_positive(self) -> DecayConfig:
        missing_sources = {"test", "fsp", "regular"} - set(self.half_life_days)
        missing_layers = set(LAYER_ORDER) - set(self.layer_factor)
        if missing_sources or missing_layers:
            raise ValueError(
                f"decay: не заданы источники {sorted(missing_sources)} "
                f"/ слои {sorted(missing_layers)}"
            )
        if any(v <= 0 for v in self.half_life_days.values()) or any(
            v <= 0 for v in self.layer_factor.values()
        ):
            raise ValueError("decay: полураспады и коэффициенты слоёв должны быть > 0")
        return self


class WeightStep(_Strict):
    min_score: float = Field(ge=0, le=1)
    w0: float = Field(ge=0, le=1)


class WeightsConfig(_Strict):
    test: list[WeightStep] = Field(min_length=1)
    regular: list[WeightStep] = Field(min_length=1)
    demo_multiplier: float = Field(default=0.85, gt=0, le=1)

    @model_validator(mode="after")
    def _descending(self) -> WeightsConfig:
        for name, steps in (("test", self.test), ("regular", self.regular)):
            scores = [step.min_score for step in steps]
            if scores != sorted(scores, reverse=True) or len(set(scores)) != len(scores):
                raise ValueError(f"weights.{name}: ступени должны идти по убыванию min_score")
        return self

    def steps(self, source: str) -> list[tuple[float, float]]:
        """Ступени (min_score, w0) для `strength.w0_for_score`."""
        items = self.test if source == "test" else self.regular
        return [(step.min_score, step.w0) for step in items]


class Thresholds(_Strict):
    active: float = Field(default=0.5, gt=0, le=1)
    fresh: float = Field(default=0.8, gt=0, le=1)
    dim: float = Field(default=0.5, gt=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Thresholds:
        if self.dim > self.fresh:
            raise ValueError("thresholds: dim не может превышать fresh")
        return self


class SpecMeta(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    status: SpecStatus
    graph_version: str = Field(min_length=1)


class GraphFile(_Strict):
    """Содержимое `<spec>/graph.yaml` с внутренними инвариантами одной специализации."""

    specialization: SpecMeta
    layers: list[LayerDef] = Field(min_length=1)
    decay: DecayConfig
    weights: WeightsConfig
    thresholds: Thresholds = Thresholds()
    nodes: list[NodeDef] = Field(min_length=1)
    edges: list[EdgeDef] = Field(min_length=1)
    bridges: list[EdgeDef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _structure(self) -> GraphFile:
        problems: list[str] = []
        spec = self.specialization.code
        layer_codes = [layer.code for layer in self.layers]
        if len(set(layer_codes)) != len(layer_codes):
            problems.append("слои повторяются")

        by_id: dict[str, NodeDef] = {}
        codes: set[str] = set()
        for node in self.nodes:
            match = NODE_ID_RE.match(node.id)
            if match is None:
                problems.append(f"узел {node.id!r}: id должен иметь вид '<spec>.<D|B|C><n>'")
                continue
            if match["spec"] != spec:
                problems.append(f"узел {node.id}: префикс не совпадает со специализацией {spec}")
            if match["code"] != node.code:
                problems.append(f"узел {node.id}: code {node.code!r} не совпадает с id")
            if match["layer"] != node.layer:
                problems.append(f"узел {node.id}: слой {node.layer} не совпадает с кодом")
            if node.layer not in layer_codes:
                problems.append(f"узел {node.id}: слой {node.layer} не объявлен в layers")
            if node.id in by_id:
                problems.append(f"узел {node.id} объявлен дважды")
            if node.code in codes:
                problems.append(f"код {node.code} повторяется в специализации {spec}")
            by_id[node.id] = node
            codes.add(node.code)

        seen_edges: set[frozenset[str]] = set()
        covered: set[str] = set()
        layer_index = {code: idx for idx, code in enumerate(LAYER_ORDER)}
        for edge in self.edges:
            label = f"ребро {edge.source}–{edge.target}"
            if edge.kind == "bridge":
                problems.append(f"{label}: мосты объявляются в секции bridges")
                continue
            if edge.source == edge.target:
                problems.append(f"{label}: петля")
                continue
            if edge.source not in by_id or edge.target not in by_id:
                problems.append(f"{label}: ссылается на неизвестный узел")
                continue
            if edge.key in seen_edges:
                problems.append(f"{label}: дубликат")
            seen_edges.add(edge.key)
            src_layer, dst_layer = by_id[edge.source].layer, by_id[edge.target].layer
            if edge.kind == "lateral" and src_layer != dst_layer:
                problems.append(f"{label}: боковое ребро между разными слоями")
            if (
                edge.kind == "vertical"
                and abs(layer_index[src_layer] - layer_index[dst_layer]) != 1
            ):
                problems.append(f"{label}: вертикальное ребро должно соединять соседние слои")
            covered.update((edge.source, edge.target))

        lonely = sorted(set(by_id) - covered)
        if lonely:
            problems.append(f"узлы без рёбер: {', '.join(lonely)}")

        for bridge in self.bridges:
            label = f"мост {bridge.source}–{bridge.target}"
            if bridge.kind != "bridge":
                problems.append(f"{label}: kind должен быть bridge")
            if bridge.source not in by_id:
                problems.append(f"{label}: источник должен быть узлом специализации {spec}")
            if bridge.target.startswith(f"{spec}."):
                problems.append(f"{label}: мост должен вести в другую специализацию")

        if problems:
            raise ValueError(f"graph {spec}: " + "; ".join(problems))
        return self


# --- grade_profiles.yaml -------------------------------------------------------------------


class OutsideLayerConstraint(_Strict):
    layer: Layer
    min_active: int = Field(ge=1)


class ProfileConstraints(_Strict):
    outside_layer: OutsideLayerConstraint | None = None
    layers_min_active: dict[Layer, int] | None = None
    walker: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Компактное представление для API: только заданные ограничения."""
        return self.model_dump(exclude_none=True, exclude_defaults=True)


class BudgetDef(_Strict):
    min_items: int = Field(ge=1)
    max_items: int = Field(ge=1)

    @model_validator(mode="after")
    def _ordered(self) -> BudgetDef:
        if self.min_items > self.max_items:
            raise ValueError("budget: min_items не может превышать max_items")
        return self


class _ProfileBase(_Strict):
    target_nodes: list[str] = Field(min_length=1)
    min_active: int = Field(ge=1)
    min_q: int = Field(ge=0)
    constraints: ProfileConstraints = ProfileConstraints()

    def requirements(self) -> GradeRequirements:
        """Условия профиля в виде, понятном `strength.check_grade_profile`."""
        constraints = self.constraints
        outside = (
            (constraints.outside_layer.layer, constraints.outside_layer.min_active)
            if constraints.outside_layer is not None
            else None
        )
        return GradeRequirements(
            target_nodes=tuple(self.target_nodes),
            min_active=self.min_active,
            min_q=self.min_q,
            outside_layer=outside,
            layers_min_active=dict(constraints.layers_min_active or {}),
            walker=constraints.walker,
        )


class DemoProfileDef(_ProfileBase):
    """Демо-профиль (§4.3): укороченный связный подграф из T."""


class GradeProfileDef(_ProfileBase):
    grade: Grade
    task_level: Literal[1, 2, 3]
    budget: BudgetDef
    demo: DemoProfileDef


class GradeProfilesFile(_Strict):
    version: int = Field(ge=1)
    specializations: dict[str, list[GradeProfileDef]]


# --- fsp_mapping.yaml ----------------------------------------------------------------------


class TierWeights(_Strict):
    winner: float = Field(ge=0, le=1)
    prize: float = Field(ge=0, le=1)
    finalist: float = Field(ge=0, le=1)
    participant: float = Field(ge=0, le=1)


class TeamBonus(_Strict):
    node_code: str
    weight: float = Field(gt=0, le=1)


class ParticipationOnly(_Strict):
    w0: float = Field(gt=0, le=1)
    level: Literal[1, 2, 3] = 1


class NodeRef(_Strict):
    id: str | None = None  # абсолютный id (node_scope: fixed)
    code: str | None = None  # код в специализации кандидата (node_scope: candidate_specialization)
    weight: float = Field(default=1.0, gt=0, le=1)

    @model_validator(mode="after")
    def _one_of(self) -> NodeRef:
        if (self.id is None) == (self.code is None):
            raise ValueError("nodes[]: задайте ровно одно из полей id или code")
        return self


class PercentileRule(_Strict):
    top_percent: float = Field(gt=0, le=100)
    level: Literal[1, 2, 3]


class LevelRule(_Strict):
    by: Literal["percentile", "tier"]
    percentile: list[PercentileRule] | None = None
    tier: dict[Tier, Literal[1, 2, 3]] | None = None
    default: Literal[1, 2, 3] = 1

    @model_validator(mode="after")
    def _matches_kind(self) -> LevelRule:
        if self.by == "percentile" and not self.percentile:
            raise ValueError("level.by=percentile требует список percentile")
        if self.by == "tier" and not self.tier:
            raise ValueError("level.by=tier требует словарь tier")
        return self


class DisciplineDef(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    match: list[str] = Field(min_length=1)
    node_scope: Literal["fixed", "candidate_specialization"]
    specializations: list[str] = Field(default_factory=list)
    fallback_specialization: str | None = None
    nodes: list[NodeRef] = Field(min_length=1)
    level: LevelRule

    @model_validator(mode="after")
    def _scope(self) -> DisciplineDef:
        fixed = self.node_scope == "fixed"
        if fixed and any(ref.id is None for ref in self.nodes):
            raise ValueError(f"дисциплина {self.code}: при node_scope=fixed узлы задаются по id")
        if not fixed and (
            any(ref.code is None for ref in self.nodes)
            or not self.specializations
            or self.fallback_specialization is None
        ):
            raise ValueError(
                f"дисциплина {self.code}: при node_scope=candidate_specialization нужны "
                "коды узлов, список specializations и fallback_specialization"
            )
        return self


class FspMappingFile(_Strict):
    version: int = Field(ge=1)
    half_life_days: float = Field(gt=0)
    tier_weights: TierWeights
    team_bonus: TeamBonus
    participation_only: ParticipationOnly
    disciplines: list[DisciplineDef] = Field(min_length=1)


# --- keywords.yaml, industries.yaml --------------------------------------------------------


class UnsupportedSpecialization(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    nearest: str
    keywords: list[str] = Field(min_length=1)


class KeywordsFile(_Strict):
    version: int = Field(ge=1)
    specializations: dict[str, list[str]]
    nodes: dict[str, list[str]]
    unsupported_specializations: list[UnsupportedSpecialization] = Field(default_factory=list)


class IndustryDef(_Strict):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)


class IndustriesFile(_Strict):
    version: int = Field(ge=1)
    industries: list[IndustryDef] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> IndustriesFile:
        codes = [item.code for item in self.industries]
        if len(set(codes)) != len(codes):
            raise ValueError("industries: коды отраслей повторяются")
        return self


# --- собранный каталог ---------------------------------------------------------------------


@dataclass(frozen=True)
class SpecGraph:
    """Граф одной специализации с производными индексами."""

    file: GraphFile
    nodes_by_id: dict[str, NodeDef]
    adjacency: dict[str, frozenset[str]]  # только рёбра внутри специализации

    @property
    def meta(self) -> SpecMeta:
        return self.file.specialization

    @property
    def code(self) -> str:
        return self.file.specialization.code

    def layer_of(self, node_id: str) -> str:
        return self.nodes_by_id[node_id].layer


@dataclass(frozen=True)
class SkillsCatalog:
    """Весь справочник: специализации (active раньше preview), мосты, профили, словари."""

    specializations: dict[str, SpecGraph]
    bridges: tuple[EdgeDef, ...]
    grade_profiles: dict[str, tuple[GradeProfileDef, ...]]
    fsp_mapping: FspMappingFile
    keywords: KeywordsFile
    industries: tuple[IndustryDef, ...]
    source_dir: Path
    _bridge_adjacency: dict[str, frozenset[str]] = field(default_factory=dict, repr=False)

    def spec(self, code: str) -> SpecGraph | None:
        return self.specializations.get(code)

    def has_node(self, node_id: str) -> bool:
        spec = self.specializations.get(node_id.split(".", 1)[0])
        return spec is not None and node_id in spec.nodes_by_id

    def node(self, node_id: str) -> NodeDef:
        spec = self.specializations.get(node_id.split(".", 1)[0])
        if spec is None or node_id not in spec.nodes_by_id:
            raise KeyError(node_id)
        return spec.nodes_by_id[node_id]

    def iter_nodes(self) -> Iterator[NodeDef]:
        for spec in self.specializations.values():
            yield from spec.file.nodes

    def adjacency(self, spec_code: str | None = None) -> dict[str, frozenset[str]]:
        """Смежность по рёбрам внутри специализации (или всех специализаций без мостов)."""
        if spec_code is not None:
            return self.specializations[spec_code].adjacency
        merged: dict[str, frozenset[str]] = {}
        for spec in self.specializations.values():
            merged.update(spec.adjacency)
        return merged

    @property
    def bridge_adjacency(self) -> dict[str, frozenset[str]]:
        """Смежность по мостам (в обе стороны) — для форм профиля T/П и бейджа «хаб»."""
        return self._bridge_adjacency

    def bridges_for(self, spec_code: str) -> list[EdgeDef]:
        """Мосты, у которых хотя бы один конец — в указанной специализации."""
        prefix = f"{spec_code}."
        return [
            bridge
            for bridge in self.bridges
            if bridge.source.startswith(prefix) or bridge.target.startswith(prefix)
        ]

    def profiles(self, spec_code: str) -> tuple[GradeProfileDef, ...]:
        return self.grade_profiles.get(spec_code, ())

    def profile(self, spec_code: str, grade: str) -> GradeProfileDef | None:
        return next((p for p in self.profiles(spec_code) if p.grade == grade), None)


# --- загрузка ------------------------------------------------------------------------------


def _read_yaml(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except FileNotFoundError as exc:
        raise SkillsCatalogError(f"файл справочника не найден: {path}") from exc
    except yaml.YAMLError as exc:
        raise SkillsCatalogError(f"{path.name}: некорректный YAML: {exc}") from exc


def _parse[T: BaseModel](model: type[T], path: Path) -> T:
    data = _read_yaml(path)
    if not isinstance(data, dict):
        raise SkillsCatalogError(f"{path}: ожидался YAML-объект")
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        raise SkillsCatalogError(f"{path}: {exc}") from exc


def _build_spec(graph: GraphFile) -> SpecGraph:
    adjacency: dict[str, set[str]] = {node.id: set() for node in graph.nodes}
    for edge in graph.edges:
        adjacency[edge.source].add(edge.target)
        adjacency[edge.target].add(edge.source)
    return SpecGraph(
        file=graph,
        nodes_by_id={node.id: node for node in graph.nodes},
        adjacency={node: frozenset(neigh) for node, neigh in adjacency.items()},
    )


def _discover_graph_files(skills_dir: Path) -> list[Path]:
    if not skills_dir.is_dir():
        raise SkillsCatalogError(f"каталог справочника не найден: {skills_dir}")
    files = sorted(
        path
        for path in skills_dir.glob(f"*/{GRAPH_FILE}")
        if path.parent.name != TEMPLATES_DIR and not path.parent.name.startswith((".", "_"))
    )
    if not files:
        raise SkillsCatalogError(f"в {skills_dir} нет ни одного <spec>/{GRAPH_FILE}")
    return files


def _validate_catalog(
    specs: dict[str, SpecGraph],
    bridges: list[EdgeDef],
    profiles: GradeProfilesFile,
    fsp: FspMappingFile,
    keywords: KeywordsFile,
) -> list[str]:
    problems: list[str] = []

    def exists(node_id: str) -> bool:
        spec = specs.get(node_id.split(".", 1)[0])
        return spec is not None and node_id in spec.nodes_by_id

    seen_bridges: set[frozenset[str]] = set()
    for bridge in bridges:
        label = f"мост {bridge.source}–{bridge.target}"
        if not exists(bridge.target):
            problems.append(f"{label}: целевой узел не найден в загруженных специализациях")
        if bridge.key in seen_bridges:
            problems.append(f"{label}: дубликат")
        seen_bridges.add(bridge.key)

    for spec_code, items in profiles.specializations.items():
        spec = specs.get(spec_code)
        if spec is None:
            problems.append(f"grade_profiles: специализация {spec_code} не найдена")
            continue
        grades = [item.grade for item in items]
        if len(set(grades)) != len(grades):
            problems.append(f"grade_profiles {spec_code}: грейды повторяются")
        for item in items:
            label = f"профиль {spec_code}/{item.grade}"
            problems.extend(_validate_profile(label, item, spec))
            problems.extend(_validate_profile(f"{label} (demo)", item.demo, spec))
            extra = set(item.demo.target_nodes) - set(item.target_nodes)
            if extra:
                problems.append(f"{label}: демо-узлы вне T: {sorted(extra)}")
            if item.budget.min_items < len(item.target_nodes):
                problems.append(f"{label}: budget.min_items меньше |T| (одно задание на узел)")

    for discipline in fsp.disciplines:
        label = f"fsp_mapping/{discipline.code}"
        for spec_code in [*discipline.specializations, discipline.fallback_specialization]:
            if spec_code is not None and spec_code not in specs:
                problems.append(f"{label}: специализация {spec_code} не найдена")
        for ref in discipline.nodes:
            if ref.id is not None and not exists(ref.id):
                problems.append(f"{label}: узел {ref.id} не найден")
            if ref.code is not None:
                for spec_code in discipline.specializations:
                    if (
                        spec_code in specs
                        and f"{spec_code}.{ref.code}" not in specs[spec_code].nodes_by_id
                    ):
                        problems.append(f"{label}: узла {ref.code} нет в {spec_code}")
    for spec in specs.values():
        if f"{spec.code}.{fsp.team_bonus.node_code}" not in spec.nodes_by_id:
            problems.append(
                f"fsp_mapping.team_bonus: узла {fsp.team_bonus.node_code} нет в {spec.code}"
            )

    for spec_code in keywords.specializations:
        if spec_code not in specs:
            problems.append(f"keywords.specializations: специализация {spec_code} не найдена")
    for node_id, words in keywords.nodes.items():
        if not exists(node_id):
            problems.append(f"keywords.nodes: узел {node_id} не найден")
        if not words:
            problems.append(f"keywords.nodes: у узла {node_id} нет ключевых слов")
    for spec in specs.values():
        missing = sorted(set(spec.nodes_by_id) - set(keywords.nodes))
        if missing:
            problems.append(f"keywords.nodes: нет слов для узлов {', '.join(missing)}")
    for item in keywords.unsupported_specializations:
        if item.nearest not in specs:
            problems.append(f"keywords.unsupported/{item.code}: nearest {item.nearest} не найдена")
        if item.code in specs:
            problems.append(
                f"keywords.unsupported/{item.code}: совпадает с загруженной специализацией"
            )

    return problems


def _validate_profile(label: str, profile: _ProfileBase, spec: SpecGraph) -> list[str]:
    problems: list[str] = []
    targets = profile.target_nodes
    unknown = [node for node in targets if node not in spec.nodes_by_id]
    if unknown:
        return [f"{label}: узлы {unknown} не принадлежат специализации {spec.code}"]
    if len(set(targets)) != len(targets):
        problems.append(f"{label}: узлы T повторяются")
    if profile.min_active > len(targets):
        problems.append(f"{label}: min_active больше |T|")
    if profile.min_q > len(targets) - 1:
        problems.append(f"{label}: min_q больше |T|−1")
    if not is_connected(targets, spec.adjacency):
        problems.append(f"{label}: целевой подграф не связен")
    check = check_grade_profile(profile.requirements(), targets, spec.adjacency, spec.layer_of)
    if not check.passed:
        problems.append(
            f"{label}: профиль непроходим даже при всех активных узлах "
            f"({', '.join(check.failed_checks)})"
        )
    return problems


def load_catalog(skills_dir: Path | str | None = None) -> SkillsCatalog:
    """Читает и валидирует весь справочник; при ошибках — SkillsCatalogError со списком."""
    root = Path(skills_dir) if skills_dir is not None else get_settings().skills_path
    root = root.resolve()

    graphs = [_parse(GraphFile, path) for path in _discover_graph_files(root)]
    specs: dict[str, SpecGraph] = {}
    for graph in graphs:
        code = graph.specialization.code
        if code in specs:
            raise SkillsCatalogError(f"специализация {code} объявлена дважды")
        specs[code] = _build_spec(graph)
    # порядок выдачи: active раньше preview, внутри — по коду
    ordered = dict(
        sorted(specs.items(), key=lambda item: (item[1].meta.status != "active", item[0]))
    )

    bridges = [bridge for graph in graphs for bridge in graph.bridges]
    profiles = _parse(GradeProfilesFile, root / GRADE_PROFILES_FILE)
    fsp = _parse(FspMappingFile, root / FSP_MAPPING_FILE)
    keywords = _parse(KeywordsFile, root / KEYWORDS_FILE)
    industries = _parse(IndustriesFile, root / INDUSTRIES_FILE)

    problems = _validate_catalog(ordered, bridges, profiles, fsp, keywords)
    if problems:
        raise SkillsCatalogError("справочник навыков: " + "; ".join(problems))

    bridge_adjacency: dict[str, set[str]] = {}
    for bridge in bridges:
        bridge_adjacency.setdefault(bridge.source, set()).add(bridge.target)
        bridge_adjacency.setdefault(bridge.target, set()).add(bridge.source)

    return SkillsCatalog(
        specializations=ordered,
        bridges=tuple(bridges),
        grade_profiles={
            code: tuple(sorted(items, key=lambda p: GRADE_ORDER.index(p.grade)))
            for code, items in profiles.specializations.items()
        },
        fsp_mapping=fsp,
        keywords=keywords,
        industries=tuple(industries.industries),
        source_dir=root,
        _bridge_adjacency={k: frozenset(v) for k, v in bridge_adjacency.items()},
    )


@lru_cache(maxsize=4)
def _load_cached(skills_dir: str) -> SkillsCatalog:
    return load_catalog(skills_dir)


def get_catalog() -> SkillsCatalog:
    """Справочник из settings.skills_path, загруженный один раз на процесс (FastAPI-зависимость)."""
    return _load_cached(str(get_settings().skills_path.resolve()))


def reset_catalog_cache() -> None:
    """Сбросить кэш (тесты, смена SKILLS_DIR)."""
    _load_cached.cache_clear()
