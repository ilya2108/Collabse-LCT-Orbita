"""Шаблоны заданий «узел × уровень» (PLAN.md §4.1): схема, загрузчик YAML и выборка.

Шаблон — единица банка заданий. Он фиксирует проверяемую концепцию, инвариант условия,
ловушку и рубрику; конкретные варианты (условие, код, опции) генерируются по нему
(`variant_generate`) и судятся относительно эталона `anchor_variant` (`variant_judge`).
Файлы лежат в `app/skills/<spec>/templates/<NODE>/L<level>_<slug>.yaml`; формат и правила
вычитки описаны в `docs/templates-spec.md`.

Модуль не ходит в БД: каталог читается с диска один раз на процесс (`get_catalog`,
lru_cache) и отдаётся роутеру `/assessment/templates`, инструменту `pool` и движку сессии.
Любая ошибка формата останавливает загрузку целиком (`TemplateLoadError` со списком проблем
по файлам): битый шаблон не должен молча выпасть из банка. Отсутствие `graph.yaml`
специализации — не ошибка, а предупреждение (проверка узла пропускается).
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.core.config import get_settings

logger = logging.getLogger("app.assessment.templates")

TemplateKind = Literal["mcq_reasoned", "code_review", "short_design", "scenario_personal"]
TemplateStatus = Literal["draft", "reviewed"]
OptionKey = Literal["A", "B", "C", "D"]

KINDS: tuple[str, ...] = ("mcq_reasoned", "code_review", "short_design", "scenario_personal")
STATUSES: tuple[str, ...] = ("draft", "reviewed")
OPTION_KEYS: tuple[str, ...] = ("A", "B", "C", "D")
MCQ_OPTIONS_COUNT = 4
CODE_REVIEW_MIN_LINES = 15
CODE_REVIEW_MAX_LINES = 40
RUBRIC_WEIGHT_TOLERANCE = 1e-6

# доля балла, проверяемая детерминированно (по ключу), остальное — судья по рубрике (§4.1)
DETERMINISTIC_SHARE: dict[str, float] = {
    "mcq_reasoned": 0.7,
    "code_review": 0.5,
    "short_design": 0.0,
    "scenario_personal": 0.0,
}

_SLUG = r"[a-z0-9]+(?:_[a-z0-9]+)*"
ID_RE = re.compile(
    rf"^(?P<spec>[a-z]{{2,16}})\.(?P<code>[DBC]\d)\.L(?P<level>[123])\.(?P<slug>{_SLUG})$"
)
NODE_RE = re.compile(r"^(?P<spec>[a-z]{2,16})\.(?P<code>[DBC]\d)$")
FILE_RE = re.compile(rf"^L(?P<level>[123])_(?P<slug>{_SLUG})\.yaml$")
RUBRIC_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
STACK_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9+.#-]{0,31}$")
DEFECT_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]{2,47}$")

TEMPLATES_DIRNAME = "templates"
GRAPH_FILENAME = "graph.yaml"


# --- схема шаблона -------------------------------------------------------------------------


class RubricCriterion(BaseModel):
    """Критерий рубрики: судья отвечает hit ∈ {0, 0.5, 1}, балл = Σ w·hit."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=RUBRIC_ID_RE.pattern)
    w: float = Field(gt=0, le=1)
    text: str = Field(min_length=10)


class McqOption(BaseModel):
    """Опция mcq_reasoned; ключ — буква в порядке эталона, при показе опции перемешиваются."""

    model_config = ConfigDict(extra="forbid")

    key: OptionKey
    text: str = Field(min_length=3)


class CodeReviewKey(BaseModel):
    """Ключ code_review: каноническая строка дефекта и строки, засчитываемые как верные
    (например, строка запроса без eager-load при N+1, проявившемся в цикле)."""

    model_config = ConfigDict(extra="forbid")

    line: int = Field(ge=1)
    accepted_lines: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> CodeReviewKey:
        lines = {self.line, *self.accepted_lines}
        if any(n < 1 for n in lines):
            raise ValueError("accepted_lines: номера строк начинаются с 1")
        self.accepted_lines = sorted(lines)
        return self

    def accepts(self, line: int) -> bool:
        return line in self.accepted_lines


class AnchorVariant(BaseModel):
    """Эталонный вариант: по нему судья сверяет сгенерированные варианты (§4.2)."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=40)
    code: str | None = None
    options: list[McqOption] | None = None
    answer_key: OptionKey | CodeReviewKey | None = None
    reference_answer: str = Field(min_length=40)
    estimated_minutes: int = Field(ge=1, le=20)

    @property
    def code_line_count(self) -> int:
        if not self.code:
            return 0
        return len(self.code.rstrip("\n").split("\n"))


class DifficultyAnchor(BaseModel):
    """Ожидаемая доля верных ответов по грейдам; монотонна по грейду."""

    model_config = ConfigDict(extra="forbid")

    p_junior: float = Field(ge=0, le=1)
    p_middle: float = Field(ge=0, le=1)
    p_senior: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _monotone(self) -> DifficultyAnchor:
        if not (self.p_junior <= self.p_middle <= self.p_senior):
            raise ValueError("difficulty_anchor: ожидается p_junior ≤ p_middle ≤ p_senior")
        return self


class ReviewInfo(BaseModel):
    """Кто и когда вычитал шаблон (заполняется при переводе в status: reviewed)."""

    model_config = ConfigDict(extra="forbid")

    by: str | None = None
    at: date | None = None
    notes: str | None = None


class TaskTemplate(BaseModel):
    """Шаблон задания «узел × уровень» — все поля PLAN.md §4.1 плюс служебные
    (`title`, `defect_class`, `max_answer_chars`, `author`, `review`)."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=ID_RE.pattern)
    node: str = Field(pattern=NODE_RE.pattern)
    level: int = Field(ge=1, le=3)
    kind: TemplateKind
    title: str = Field(min_length=5, max_length=120)
    concept: str = Field(min_length=20)
    invariant: str = Field(min_length=20)
    vary: str = Field(min_length=10)
    forbidden_changes: str = Field(min_length=10)
    trap: str = Field(min_length=10)
    # класс дефекта для code_review (n_plus_one, race_condition, stale_derived_state, …)
    defect_class: str | None = Field(default=None, pattern=DEFECT_CLASS_RE.pattern)
    stack_tags: list[str] = Field(min_length=1, max_length=8)
    rubric: list[RubricCriterion] = Field(min_length=2, max_length=6)
    anchor_variant: AnchorVariant
    difficulty_anchor: DifficultyAnchor
    max_time_sec: int = Field(ge=60, le=900)
    # лимит длины открытого ответа кандидата (short_design / обоснование)
    max_answer_chars: int = Field(default=800, ge=100, le=3000)
    status: TemplateStatus = "draft"
    author: str | None = None
    review: ReviewInfo | None = None

    # --- производные ---------------------------------------------------------------------

    @property
    def spec(self) -> str:
        return self.node.split(".", 1)[0]

    @property
    def node_code(self) -> str:
        return self.node.split(".", 1)[1]

    @property
    def slug(self) -> str:
        return self.id.rsplit(".", 1)[1]

    @property
    def deterministic_share(self) -> float:
        return DETERMINISTIC_SHARE[self.kind]

    # --- инварианты ----------------------------------------------------------------------

    @model_validator(mode="after")
    def _check_identity(self) -> TaskTemplate:
        expected_prefix = f"{self.node}.L{self.level}."
        if not self.id.startswith(expected_prefix):
            raise ValueError(
                f"id {self.id!r} должен начинаться с {expected_prefix!r} (node + уровень)"
            )
        return self

    @model_validator(mode="after")
    def _check_tags(self) -> TaskTemplate:
        bad = [t for t in self.stack_tags if not STACK_TAG_RE.match(t)]
        if bad:
            raise ValueError(f"stack_tags: недопустимые теги {bad} (строчные латиница/цифры)")
        if len(set(self.stack_tags)) != len(self.stack_tags):
            raise ValueError("stack_tags: теги повторяются")
        return self

    @model_validator(mode="after")
    def _check_rubric(self) -> TaskTemplate:
        ids = [c.id for c in self.rubric]
        if len(set(ids)) != len(ids):
            raise ValueError(f"rubric: повторяющиеся id критериев {ids}")
        total = sum(c.w for c in self.rubric)
        if not math.isclose(total, 1.0, abs_tol=RUBRIC_WEIGHT_TOLERANCE):
            raise ValueError(f"rubric: сумма весов должна быть 1, сейчас {total:.4f}")
        return self

    @model_validator(mode="after")
    def _check_time(self) -> TaskTemplate:
        if self.anchor_variant.estimated_minutes * 60 > self.max_time_sec:
            raise ValueError(
                "anchor_variant.estimated_minutes превышает max_time_sec "
                f"({self.anchor_variant.estimated_minutes} мин > {self.max_time_sec} с)"
            )
        return self

    @model_validator(mode="after")
    def _check_kind(self) -> TaskTemplate:
        anchor = self.anchor_variant
        if self.kind == "mcq_reasoned":
            self._check_mcq(anchor)
        elif self.kind == "code_review":
            self._check_code_review(anchor)
        else:
            if anchor.options is not None:
                raise ValueError(f"{self.kind}: поле options не используется")
            if anchor.answer_key is not None:
                raise ValueError(f"{self.kind}: поле answer_key не используется (судья по рубрике)")
        if self.kind != "code_review" and self.defect_class is not None:
            raise ValueError("defect_class задаётся только для code_review")
        return self

    @staticmethod
    def _check_mcq(anchor: AnchorVariant) -> None:
        if not anchor.options or len(anchor.options) != MCQ_OPTIONS_COUNT:
            raise ValueError(f"mcq_reasoned: ровно {MCQ_OPTIONS_COUNT} опции в anchor_variant")
        keys = [o.key for o in anchor.options]
        if sorted(keys) != list(OPTION_KEYS):
            raise ValueError(f"mcq_reasoned: ключи опций должны быть {OPTION_KEYS}, сейчас {keys}")
        texts = [o.text.strip().lower() for o in anchor.options]
        if len(set(texts)) != len(texts):
            raise ValueError("mcq_reasoned: опции повторяются")
        if not isinstance(anchor.answer_key, str):
            raise ValueError("mcq_reasoned: answer_key — одна буква из ключей опций")

    def _check_code_review(self, anchor: AnchorVariant) -> None:
        if not self.defect_class:
            raise ValueError("code_review: обязателен defect_class")
        if not anchor.code or not anchor.code.strip():
            raise ValueError("code_review: обязателен anchor_variant.code")
        lines = anchor.code_line_count
        if not (CODE_REVIEW_MIN_LINES <= lines <= CODE_REVIEW_MAX_LINES):
            raise ValueError(
                f"code_review: фрагмент {CODE_REVIEW_MIN_LINES}–{CODE_REVIEW_MAX_LINES} строк, "
                f"сейчас {lines}"
            )
        if anchor.options is not None:
            raise ValueError("code_review: поле options не используется")
        key = anchor.answer_key
        if not isinstance(key, CodeReviewKey):
            raise ValueError("code_review: answer_key — объект {line, accepted_lines}")
        out_of_range = [n for n in key.accepted_lines if n > lines]
        if out_of_range:
            raise ValueError(f"code_review: строки {out_of_range} вне фрагмента ({lines} строк)")


# --- загрузчик ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class TemplateProblem:
    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


class TemplateLoadError(ValueError):
    """Один или несколько шаблонов не прошли валидацию; `problems` — по файлам."""

    def __init__(self, problems: list[TemplateProblem]) -> None:
        self.problems = problems
        super().__init__("Ошибки в шаблонах заданий:\n" + "\n".join(f"  - {p}" for p in problems))


@dataclass(frozen=True)
class TemplateCatalog:
    """Загруженный каталог: шаблоны в детерминированном порядке и предупреждения загрузки."""

    skills_path: Path
    templates: tuple[TaskTemplate, ...]
    warnings: tuple[str, ...] = ()

    def by_id(self, template_id: str) -> TaskTemplate | None:
        for template in self.templates:
            if template.id == template_id:
                return template
        return None

    @property
    def ids(self) -> list[str]:
        return [t.id for t in self.templates]


def format_validation_error(exc: ValidationError) -> str:
    """Короткая человекочитаемая сводка ошибок Pydantic: `поле: сообщение`."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "<root>"
        msg = err.get("msg", "")
        # pydantic оборачивает ValueError из валидаторов префиксом «Value error, »
        msg = msg.removeprefix("Value error, ")
        parts.append(f"{loc}: {msg}")
    return "; ".join(parts)


def _extract_graph_node_ids(data: Any, spec: str) -> set[str]:
    """Идентификаторы узлов из graph.yaml (терпимо к форме: список словарей с id/code,
    список строк или словарь code → узел)."""
    ids: set[str] = set()
    nodes = data.get("nodes") if isinstance(data, dict) else None
    if isinstance(nodes, list):
        for item in nodes:
            node_id: str | None = None
            if isinstance(item, dict):
                raw = item.get("id") or item.get("code")
                node_id = str(raw) if raw else None
            elif isinstance(item, str):
                node_id = item
            if node_id:
                ids.add(node_id if "." in node_id else f"{spec}.{node_id}")
    elif isinstance(nodes, dict):
        for key, item in nodes.items():
            raw = item.get("id") if isinstance(item, dict) and item.get("id") else str(key)
            ids.add(raw if "." in raw else f"{spec}.{raw}")
    return ids


def read_graph_node_ids(skills_path: Path, spec: str) -> set[str] | None:
    """Узлы графа специализации или None, если graph.yaml ещё нет / не читается."""
    graph_path = skills_path / spec / GRAPH_FILENAME
    if not graph_path.is_file():
        return None
    try:
        data = yaml.safe_load(graph_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("graph.yaml специализации %s не разобран: %s", spec, exc)
        return None
    ids = _extract_graph_node_ids(data, spec)
    return ids or None


def iter_template_files(skills_path: Path) -> list[Path]:
    """Все YAML шаблонов в каталоге навыков, в стабильном порядке."""
    pattern = f"*/{TEMPLATES_DIRNAME}/*/L*_*.yaml"
    return sorted(p for p in skills_path.glob(pattern) if p.is_file())


def parse_template_file(path: Path, skills_path: Path | None = None) -> TaskTemplate:
    """Читает и валидирует один шаблон; при `skills_path` сверяет id/node/level с путём."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise TemplateLoadError([TemplateProblem(str(path), f"YAML не разобран: {exc}")]) from exc
    if not isinstance(data, dict):
        raise TemplateLoadError([TemplateProblem(str(path), "ожидался YAML-объект (словарь)")])
    try:
        template = TaskTemplate.model_validate(data)
    except ValidationError as exc:
        raise TemplateLoadError([TemplateProblem(str(path), format_validation_error(exc))]) from exc
    if skills_path is not None:
        problem = _path_mismatch(template, path, skills_path)
        if problem:
            raise TemplateLoadError([TemplateProblem(str(path), problem)])
    return template


def _path_mismatch(template: TaskTemplate, path: Path, skills_path: Path) -> str | None:
    """Файл `<spec>/templates/<NODE>/L<level>_<slug>.yaml` должен совпадать с полями."""
    try:
        parts = path.resolve().relative_to(skills_path.resolve()).parts
    except ValueError:
        return f"файл вне каталога навыков {skills_path}"
    if len(parts) != 4 or parts[1] != TEMPLATES_DIRNAME:
        return "ожидается путь <spec>/templates/<NODE>/L<level>_<slug>.yaml"
    spec, _, node_code, filename = parts
    match = FILE_RE.match(filename)
    if not match:
        return "имя файла должно быть L<1|2|3>_<slug>.yaml"
    expected_node = f"{spec}.{node_code}"
    if template.node != expected_node:
        return f"node {template.node!r} не совпадает с каталогом {expected_node!r}"
    if int(match["level"]) != template.level:
        return f"уровень в имени файла L{match['level']} ≠ level: {template.level}"
    if match["slug"] != template.slug:
        return f"slug в имени файла {match['slug']!r} ≠ slug в id {template.slug!r}"
    return None


def load_catalog(skills_path: Path | None = None) -> TemplateCatalog:
    """Загружает все шаблоны из `skills_path` (по умолчанию settings.skills_path).

    Проверяется каждый файл (схема + соответствие пути), уникальность id и наличие узла
    в `graph.yaml` той же специализации (если граф есть). Все проблемы собираются
    и отдаются одной `TemplateLoadError`.
    """
    root = (skills_path or get_settings().skills_path).resolve()
    problems: list[TemplateProblem] = []
    warnings: list[str] = []
    templates: list[TaskTemplate] = []
    seen: dict[str, Path] = {}
    graph_cache: dict[str, set[str] | None] = {}

    for path in iter_template_files(root):
        try:
            template = parse_template_file(path, root)
        except TemplateLoadError as exc:
            problems.extend(exc.problems)
            continue
        if template.id in seen:
            problems.append(
                TemplateProblem(str(path), f"id {template.id!r} уже объявлен в {seen[template.id]}")
            )
            continue
        seen[template.id] = path

        spec = template.spec
        if spec not in graph_cache:
            graph_cache[spec] = read_graph_node_ids(root, spec)
            if graph_cache[spec] is None:
                warning = (
                    f"{spec}/{GRAPH_FILENAME} не найден или без узлов — "
                    "проверка существования узлов пропущена"
                )
                warnings.append(warning)
                logger.warning(warning)
        node_ids = graph_cache[spec]
        if node_ids is not None and template.node not in node_ids:
            problems.append(
                TemplateProblem(str(path), f"узел {template.node!r} отсутствует в графе {spec}")
            )
            continue
        templates.append(template)

    if problems:
        raise TemplateLoadError(problems)
    templates.sort(key=lambda t: (t.spec, t.node_code, t.level, t.slug))
    return TemplateCatalog(skills_path=root, templates=tuple(templates), warnings=tuple(warnings))


@lru_cache(maxsize=4)
def _cached_catalog(skills_path: str) -> TemplateCatalog:
    return load_catalog(Path(skills_path))


def get_catalog(skills_path: Path | None = None) -> TemplateCatalog:
    """Каталог шаблонов с кэшем на процесс (ключ — путь); `reset_catalog_cache()` сбрасывает."""
    root = (skills_path or get_settings().skills_path).resolve()
    return _cached_catalog(str(root))


def reset_catalog_cache() -> None:
    _cached_catalog.cache_clear()


def list_templates(
    spec: str | None = None,
    node: str | None = None,
    level: int | None = None,
    *,
    kind: str | None = None,
    status: str | None = None,
    catalog: TemplateCatalog | None = None,
) -> list[TaskTemplate]:
    """Шаблоны по фильтрам (все необязательны): специализация, узел (`be.B1`), уровень,
    вид, статус. Порядок — spec, узел, уровень, slug."""
    source = catalog or get_catalog()
    result = []
    for template in source.templates:
        if spec is not None and template.spec != spec:
            continue
        if node is not None and template.node != node:
            continue
        if level is not None and template.level != level:
            continue
        if kind is not None and template.kind != kind:
            continue
        if status is not None and template.status != status:
            continue
        result.append(template)
    return result


def get_template(
    template_id: str, *, catalog: TemplateCatalog | None = None
) -> TaskTemplate | None:
    return (catalog or get_catalog()).by_id(template_id)
