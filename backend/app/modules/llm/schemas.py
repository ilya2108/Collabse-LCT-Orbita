"""Pydantic-схемы структурированного вывода LLM (PLAN.md §6, таблица промптов).

Каждый промпт возвращает ровно один объект по своей схеме. Схема передаётся провайдеру как
JSON Schema (structured output, где поддерживается) и дублируется словами в системной
инструкции. Политика валидации: обязательные поля и диапазоны проверяются строго, лишние
поля игнорируются (модели любят добавлять комментарии) — ошибка валидации уходит модели
текстом для повтора (`base.LLMProvider.complete`).

Схемы — контракт для модулей `matching` (NeedProfile), `assessment` (TaskVariant, JudgeReport,
Grading) и `candidates` (ResumeFacts); реестр `SCHEMAS` используется загрузчиком промптов.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Grade = Literal["junior", "middle", "senior"]


class LlmOutput(BaseModel):
    """База для всех схем ответа: лишние поля отбрасываются, строки обрезаются по краям."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


# --- need_parse -----------------------------------------------------------------------------


class NeedNode(LlmOutput):
    """Узел графа навыков, который потребность работодателя проверяет."""

    id: str = Field(min_length=3, description="id узла из каталога, например be.B1")
    min_level: int = Field(ge=1, le=3, description="минимальный уровень заданий 1..3")
    importance: float = Field(ge=0, le=1, description="важность узла для потребности 0..1")
    evidence: str = Field(default="", description="фрагмент потребности, из которого следует узел")


class NeedProfile(LlmOutput):
    """Разбор потребности работодателя в подграф навыков (промпт need_parse)."""

    # null — направление вне MVP (rules: warning `specialization_not_supported:<code>`)
    specialization: str | None = Field(
        description="код специализации: be, fe, …; null — не поддерживается"
    )
    grade: Grade
    nodes: list[NeedNode] = Field(default_factory=list)
    stack_tags: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("specialization")
    @classmethod
    def _lower_spec(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().lower()
        return cleaned or None

    @field_validator("stack_tags", "nice_to_have")
    @classmethod
    def _normalize_tags(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for value in values:
            tag = value.strip().lower()
            if tag and tag not in seen:
                seen.append(tag)
        return seen

    @model_validator(mode="after")
    def _dedupe_nodes(self) -> NeedProfile:
        """Один узел — одна запись (модель иногда повторяет id); первая запись побеждает."""
        unique: dict[str, NeedNode] = {}
        for node in self.nodes:
            unique.setdefault(node.id, node)
        self.nodes = list(unique.values())
        return self


# --- variant_generate / scenario_personal ---------------------------------------------------


class TaskVariant(LlmOutput):
    """Вариант задания по шаблону (промпты variant_generate и scenario_personal)."""

    statement: str = Field(min_length=1, description="условие задания для кандидата")
    code: str | None = Field(default=None, description="фрагмент кода для code_review")
    options: list[str] | None = Field(default=None, description="4 опции для mcq_reasoned")
    answer_key: str | None = Field(
        default=None, description="ключ закрытой части: буква опции или номер строки"
    )
    reference_answer: str = Field(min_length=1, description="эталонный ответ для судьи")
    trap_realized: str = Field(default="", description="как в варианте реализована ловушка")
    stack_tags: list[str] = Field(default_factory=list)
    estimated_minutes: int = Field(default=3, ge=1, le=60)

    @field_validator("options")
    @classmethod
    def _options_non_empty(cls, values: list[str] | None) -> list[str] | None:
        if values is None:
            return None
        cleaned = [v for v in (x.strip() for x in values) if v]
        return cleaned or None


# --- variant_judge ----------------------------------------------------------------------------


class JudgeReport(LlmOutput):
    """Заключение судьи о варианте относительно эталона (промпт variant_judge, PLAN.md §4.2)."""

    same_concept: bool = Field(description="вариант проверяет тот же концепт, что и эталон")
    single_correct: bool = Field(description="у варианта ровно один правильный ответ")
    difficulty_delta: float = Field(ge=-2, le=2, description="сложность относительно эталона")
    ambiguity: int = Field(ge=1, le=5, description="неоднозначность условия, 1 — нет")
    has_trap: bool = Field(description="ловушка шаблона реализована")
    leak_in_statement: bool = Field(description="условие выдаёт ответ")
    reasons: list[str] = Field(default_factory=list)

    @property
    def accepted(self) -> bool:
        """Правило приёма варианта в пул (PLAN.md §4.2): концепт тот же, ответ один,
        |Δ сложности| ≤ 0.5, неоднозначность ≤ 2, ловушка есть, утечки нет."""
        return (
            self.same_concept
            and self.single_correct
            and abs(self.difficulty_delta) <= 0.5
            and self.ambiguity <= 2
            and self.has_trap
            and not self.leak_in_statement
        )


# --- answer_grade -----------------------------------------------------------------------------

HIT_VALUES = (0.0, 0.5, 1.0)


class GradingCriterion(LlmOutput):
    """Оценка одного критерия рубрики: hit ∈ {0, 0.5, 1}."""

    id: str = Field(min_length=1)
    hit: float = Field(description="0 — не раскрыт, 0.5 — частично, 1 — раскрыт")
    evidence: str = Field(default="", description="фрагмент ответа, подтверждающий оценку")

    @field_validator("hit", mode="before")
    @classmethod
    def _coerce_hit(cls, value: object) -> float:
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("hit должен быть числом 0, 0.5 или 1") from exc
        if number not in HIT_VALUES:
            raise ValueError("hit должен быть одним из 0, 0.5, 1")
        return number


class Grading(LlmOutput):
    """Оценка открытого ответа по рубрике (промпт answer_grade, PLAN.md §4.4)."""

    criteria: list[GradingCriterion] = Field(default_factory=list)
    score: float = Field(ge=0, le=1, description="итоговый балл 0..1")
    flags: list[str] = Field(default_factory=list)
    feedback_short: str = Field(default="", description="1–2 предложения кандидату")

    def weighted_score(self, rubric: list[dict]) -> float:
        """Пересчёт балла по весам рубрики `[{id, w, text}]` — защита от арифметики модели."""
        weights = {str(item.get("id")): float(item.get("w", 0) or 0) for item in rubric}
        total = sum(weights.values())
        if total <= 0:
            if not self.criteria:
                return 0.0
            return round(sum(c.hit for c in self.criteria) / len(self.criteria), 4)
        got = sum(weights.get(c.id, 0.0) * c.hit for c in self.criteria)
        return round(min(1.0, max(0.0, got / total)), 4)


# --- resume_parse -----------------------------------------------------------------------------


class ResumeClaimedNode(LlmOutput):
    """Узел, который кандидат заявляет в резюме (source: declared, в ранжировании не участвует)."""

    id: str = Field(min_length=3)
    level: int = Field(ge=1, le=3)
    evidence: str = Field(default="")


class ResumeFacts(LlmOutput):
    """Факты из текста резюме без контактов (промпт resume_parse)."""

    stack: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)
    years: float | None = Field(default=None, ge=0, le=60)
    soft_skills: list[str] = Field(default_factory=list)
    claimed_nodes: list[ResumeClaimedNode] = Field(default_factory=list)

    @field_validator("stack")
    @classmethod
    def _lower_stack(cls, values: list[str]) -> list[str]:
        seen: list[str] = []
        for value in values:
            tag = value.strip().lower()
            if tag and tag not in seen:
                seen.append(tag)
        return seen


# --- answer_bank ------------------------------------------------------------------------------


class AnswerBank(LlmOutput):
    """Три ответа разного качества на эталонный вариант — только для валидации судьи."""

    strong: str = Field(min_length=1)
    partial: str = Field(min_length=1)
    weak: str = Field(min_length=1)


# --- graph_summary / selection_summary ----------------------------------------------------------


class SummaryText(LlmOutput):
    """Свободный текстовый разбор по-русски (промпты graph_summary и selection_summary).

    Структура текста (абзацы, списки со строками «— ») задаётся промптом, а не схемой;
    схема нужна только затем, чтобы ответ шёл тем же конвейером structured output."""

    text: str = Field(min_length=1, description="текст разбора по-русски")


# --- реестр ------------------------------------------------------------------------------------

SCHEMAS: dict[str, type[BaseModel]] = {
    "NeedProfile": NeedProfile,
    "TaskVariant": TaskVariant,
    "JudgeReport": JudgeReport,
    "Grading": Grading,
    "ResumeFacts": ResumeFacts,
    "AnswerBank": AnswerBank,
    "SummaryText": SummaryText,
}

__all__ = [
    "AnswerBank",
    "Grade",
    "Grading",
    "GradingCriterion",
    "HIT_VALUES",
    "JudgeReport",
    "LlmOutput",
    "NeedNode",
    "NeedProfile",
    "ResumeClaimedNode",
    "ResumeFacts",
    "SCHEMAS",
    "SummaryText",
    "TaskVariant",
]
