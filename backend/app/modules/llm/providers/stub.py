"""Провайдер `stub`: детерминированные фикстуры для каждого промпта (CI, запуск без ключей).

Ответ строится из переменных промпта простыми эвристиками, поэтому одинаковый вход даёт
одинаковый выход, а результат проходит схему без повторов:
- need_parse — специализация и грейд по ключевым словам, базовый набор узлов;
- variant_generate / scenario_personal — вариант из эталона шаблона с контекстом;
- variant_judge — сравнение токенов эталона и варианта;
- answer_grade — эвристика по ключевым словам рубрики (PLAN.md §4.4: «режим без LLM»,
  флаг `heuristic_no_llm`) + флаг `too_close_to_reference` при совпадении с эталоном;
- resume_parse — стек, роли, стаж регулярками, заявленные узлы — по keywords.yaml;
- answer_bank — три ответа из эталона.

Stub ничего не знает о сети: `_complete` получает сообщения только ради учёта токенов,
содержимое ответа берётся из переменных (`complete()` переопределён).
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.modules.llm.base import (
    CompletionResult,
    LLMProvider,
    LLMUnsupportedError,
    RawCompletion,
    build_messages,
)
from app.modules.llm.prompts import PromptSpec
from app.modules.llm.providers.rules import (
    DEFAULT_STACK_TAGS,
    detect_grade,
    detect_stack_tags,
    find_keywords,
    load_keywords,
    match_nodes,
    tokenize,
)
from app.modules.llm.schemas import (
    AnswerBank,
    Grading,
    GradingCriterion,
    JudgeReport,
    NeedNode,
    NeedProfile,
    ResumeClaimedNode,
    ResumeFacts,
    SummaryText,
    TaskVariant,
)

STUB_MODEL = "stub-fixture"
FLAG_HEURISTIC = "heuristic_no_llm"
FLAG_TOO_CLOSE = "too_close_to_reference"
FLAG_EMPTY = "empty_answer"
FLAG_TOO_SHORT = "too_short"

_FE_HINTS = ("react", "vue", "angular", "фронт", "frontend", "front-end", "вёрстк", "верстк",
             "css", "typescript", "браузер", "ui-", "интерфейс")  # fmt: skip
_BE_HINTS = ("backend", "бэкенд", "бекенд", "python", "java", "golang", "go ", "sql",
             "postgres", "api", "сервис", "микросервис", "django", "fastapi", "spring")  # fmt: skip

_BASE_NODES = {
    "be": ("be.B1", "be.B2", "be.B3"),
    "fe": ("fe.B1", "fe.B2", "fe.B3"),
}
_EXTRA_NODES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    "be": (
        ("be.D4", ("sql", "postgres", "индекс", "баз", "субд", "транзакц")),
        ("be.C1", ("docker", "ci", "cd", "kubernetes", "поставк", "деплой")),
        ("be.B5", ("безопас", "auth", "oauth", "jwt", "owasp", "пдн")),
        ("be.C2", ("kafka", "очеред", "rabbit", "распредел", "интеграц", "кэш")),
        ("be.D3", ("async", "конкурент", "потоки", "нагруз", "highload")),
    ),
    "fe": (
        ("fe.D4", ("http", "сеть", "cors", "кэш", "api")),
        ("fe.C1", ("vite", "webpack", "ci", "сборк", "деплой")),
        ("fe.B5", ("xss", "csrf", "безопас", "auth")),
        ("fe.B4", ("тест", "jest", "vitest", "playwright", "отладк")),
        ("fe.D3", ("производительн", "рендер", "reflow", "анимац")),
    ),
}
_ROLE_HINTS = (
    "бэкенд-разработчик", "backend-разработчик", "backend developer", "фронтенд-разработчик",
    "frontend developer", "fullstack", "фулстек", "тимлид", "team lead", "техлид", "tech lead",
    "архитектор", "devops", "qa", "тестировщик", "аналитик", "data engineer", "ml engineer",
    "разработчик", "developer", "инженер",
)  # fmt: skip
_SOFT_HINTS = (
    "наставнич", "менторств", "ментор", "коммуникац", "презентац", "публичн", "лидер",
    "переговор", "планирован", "обучени", "код-ревью", "code review", "ответственн",
)  # fmt: skip
_YEARS_RE = re.compile(r"(\d{1,2})(?:[.,]\d)?\+?\s*(?:лет|года|год|years?|yrs)\b", re.IGNORECASE)
_LEVEL_RE = re.compile(r"level[\"']?\s*[:=]\s*(\d)", re.IGNORECASE)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except ValueError:
            return {"text": value}
        return data if isinstance(data, dict) else {"text": value}
    return {}


def _text_of(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, BaseModel):
        return value.model_dump_json()
    return json.dumps(value, ensure_ascii=False, default=str)


def _seed(variables: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(variables, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:8]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _ngram_similarity(answer: str, reference: str, n: int = 3) -> float:
    """Доля n-грамм ответа, встречающихся в эталоне (флаг близости к эталону, §4.4)."""
    a_words = re.findall(r"\w+", answer.lower())
    r_words = re.findall(r"\w+", reference.lower())
    if len(a_words) < n or len(r_words) < n:
        return 0.0
    a_grams = {tuple(a_words[i : i + n]) for i in range(len(a_words) - n + 1)}
    r_grams = {tuple(r_words[i : i + n]) for i in range(len(r_words) - n + 1)}
    return len(a_grams & r_grams) / len(a_grams)


# --- фикстуры по промптам ---------------------------------------------------------------------


def stub_need_parse(variables: dict[str, Any]) -> NeedProfile:
    text = str(variables.get("need_text") or variables.get("text") or "")
    hint = str(variables.get("specialization") or "").lower()
    spec = hint if hint in _BASE_NODES else None
    if spec is None:
        fe_hits = len(find_keywords(text, _FE_HINTS))
        be_hits = len(find_keywords(text, _BE_HINTS))
        spec = "fe" if fe_hits > be_hits else "be"
    grade = detect_grade(text)
    level = {"junior": 1, "middle": 2, "senior": 3}[grade]
    nodes = [
        NeedNode(id=node_id, min_level=level, importance=0.9, evidence="базовый узел грейда")
        for node_id in _BASE_NODES[spec]
    ]
    for node_id, hints in _EXTRA_NODES[spec]:
        found = find_keywords(text, hints)
        if found:
            nodes.append(
                NeedNode(
                    id=node_id,
                    min_level=level,
                    importance=0.6,
                    evidence="упоминание: " + ", ".join(found),
                )
            )
    return NeedProfile(
        specialization=spec,
        grade=grade,  # type: ignore[arg-type]
        nodes=nodes,
        stack_tags=detect_stack_tags(text, DEFAULT_STACK_TAGS),
        nice_to_have=[],
        warnings=["режим без LLM: разбор по фикстуре stub"],
    )


def _template_parts(variables: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    template = _as_dict(variables.get("template"))
    anchor = _as_dict(template.get("anchor_variant") or variables.get("anchor"))
    return template, anchor


def stub_variant_generate(variables: dict[str, Any]) -> TaskVariant:
    template, anchor = _template_parts(variables)
    seed = variables.get("context_seed")
    seed_text = (
        _text_of(seed)
        if not isinstance(seed, dict)
        else ", ".join(f"{k}: {v}" for k, v in seed.items())
    )
    statement = str(anchor.get("statement") or template.get("concept") or "Опишите решение.")
    if seed_text:
        statement = f"{statement}\n\nКонтекст варианта: {seed_text}"
    options = anchor.get("options")
    return TaskVariant(
        statement=statement,
        code=anchor.get("code"),
        options=list(options) if options else None,
        answer_key=anchor.get("answer_key"),
        reference_answer=str(anchor.get("reference_answer") or template.get("concept") or "—"),
        trap_realized=str(template.get("trap") or ""),
        stack_tags=[str(t).lower() for t in template.get("stack_tags") or []],
        estimated_minutes=int(anchor.get("estimated_minutes") or 3),
    )


def stub_scenario_personal(variables: dict[str, Any]) -> TaskVariant:
    accomplishment = _as_dict(variables.get("accomplishment"))
    template = _as_dict(variables.get("template"))
    title = str(accomplishment.get("title") or "описанный проект")
    description = str(accomplishment.get("description") or accomplishment.get("text") or "")
    concept = str(template.get("concept") or "принятое решение")
    rubric = template.get("rubric") or []
    statement = (
        f"Вы описали свершение «{title}»"
        + (f": {description}" if description else "")
        + f". Представьте, что эту задачу нужно повторить в новых условиях. Как вы подошли бы "
        f"к решению с точки зрения темы «{concept}»: что выберете, почему, какие компромиссы "
        f"и риски видите и как убедитесь, что решение работает?"
    )
    reference = "; ".join(str(item.get("text", "")) for item in rubric if isinstance(item, dict))
    return TaskVariant(
        statement=statement,
        reference_answer=reference or concept,
        trap_realized=str(template.get("trap") or ""),
        stack_tags=[str(t).lower() for t in template.get("stack_tags") or []],
        estimated_minutes=5,
    )


def stub_variant_judge(variables: dict[str, Any]) -> JudgeReport:
    template = _as_dict(variables.get("template"))
    anchor = _as_dict(variables.get("anchor"))
    variant = _as_dict(variables.get("variant"))
    a_statement = tokenize(str(anchor.get("statement", "")))
    v_statement = tokenize(str(variant.get("statement", "")))
    a_reference = tokenize(str(anchor.get("reference_answer", "")))
    v_reference = tokenize(str(variant.get("reference_answer", "")))
    reasons: list[str] = []

    concept_overlap = max(_jaccard(a_statement, v_statement), _jaccard(a_reference, v_reference))
    same_concept = concept_overlap >= 0.15 or not a_statement
    reasons.append(f"совпадение токенов с эталоном: {concept_overlap:.2f}")

    options = variant.get("options")
    single_correct = True
    if options:
        single_correct = len(options) == 4 and bool(variant.get("answer_key"))
        if not single_correct:
            reasons.append("у варианта с опциями должно быть 4 опции и ключ ответа")
    elif str(template.get("kind", "")) == "code_review" and not variant.get("answer_key"):
        single_correct = False
        reasons.append("для code_review нужен номер строки в answer_key")

    a_len = max(len(str(anchor.get("statement", ""))), 1)
    v_len = len(str(variant.get("statement", "")))
    ratio = v_len / a_len
    difficulty_delta = 0.0 if 0.6 <= ratio <= 1.6 else (0.5 if ratio > 1.6 else -0.5)
    if difficulty_delta:
        reasons.append(f"длина условия отличается от эталона в {ratio:.1f} раза")

    ambiguity = 1 if v_len >= 60 else 3
    if ambiguity > 1:
        reasons.append("условие слишком короткое, возможны разные трактовки")

    trap = str(template.get("trap") or "")
    has_trap = bool(str(variant.get("trap_realized") or "").strip()) or bool(
        trap and _jaccard(tokenize(trap), v_statement) > 0
    )
    if not has_trap:
        reasons.append("ловушка шаблона в варианте не описана")

    leak_tokens = (v_reference - a_statement) & v_statement
    leak_ratio = len(leak_tokens) / len(v_reference) if v_reference else 0.0
    leak_in_statement = leak_ratio >= 0.6
    if leak_in_statement:
        reasons.append("условие содержит большую часть формулировок эталонного ответа")
    reasons.append("заключение stub: эвристика без LLM")
    return JudgeReport(
        same_concept=same_concept,
        single_correct=single_correct,
        difficulty_delta=difficulty_delta,
        ambiguity=ambiguity,
        has_trap=has_trap,
        leak_in_statement=leak_in_statement,
        reasons=reasons,
    )


def grade_by_keywords(
    answer: str, rubric: Sequence[dict[str, Any]], reference: str = ""
) -> Grading:
    """Эвристический грейдинг по ключевым словам рубрики (чистая функция; используется и как
    запасной грейдер assessment при таймауте LLM — флаг `heuristic_no_llm`)."""
    answer_tokens = tokenize(answer)
    flags = [FLAG_HEURISTIC]
    if not answer.strip():
        flags.append(FLAG_EMPTY)
    elif len(answer.strip()) < 20:
        flags.append(FLAG_TOO_SHORT)
    criteria: list[GradingCriterion] = []
    for item in rubric:
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or f"c{len(criteria) + 1}")
        keywords = tokenize(str(item.get("text", "")))
        hit = 0.0
        evidence = ""
        if keywords and answer_tokens:
            matched = sorted(keywords & answer_tokens)
            coverage = len(matched) / len(keywords)
            if coverage >= 0.5 or len(matched) >= 3:
                hit = 1.0
            elif matched:
                hit = 0.5
            if matched:
                evidence = "совпали ключевые слова: " + ", ".join(matched[:6])
        criteria.append(GradingCriterion(id=cid, hit=hit, evidence=evidence))
    grading = Grading(criteria=criteria, score=0.0, flags=flags, feedback_short="")
    grading.score = grading.weighted_score(list(rubric))
    if reference and _ngram_similarity(answer, reference) > 0.8:
        grading.flags.append(FLAG_TOO_CLOSE)
    hit_count = sum(1 for c in criteria if c.hit >= 1.0)
    partial = sum(1 for c in criteria if c.hit == 0.5)
    grading.feedback_short = (
        f"Оценка без ИИ: засчитано {hit_count} из {len(criteria)} критериев"
        + (f", частично — {partial}" if partial else "")
        + ". Раскройте остальные пункты подробнее."
    )
    return grading


def stub_answer_grade(variables: dict[str, Any]) -> Grading:
    rubric = variables.get("rubric") or []
    if isinstance(rubric, str):
        try:
            rubric = json.loads(rubric)
        except ValueError:
            rubric = []
    variant = _as_dict(variables.get("variant"))
    return grade_by_keywords(
        str(variables.get("answer") or ""),
        [r for r in rubric if isinstance(r, dict)],
        str(variant.get("reference_answer") or ""),
    )


def stub_resume_parse(variables: dict[str, Any]) -> ResumeFacts:
    text = str(variables.get("resume_text") or variables.get("text") or "")
    years_values = [int(m.group(1)) for m in _YEARS_RE.finditer(text)]
    years = float(max(years_values)) if years_values else None
    roles = list(find_keywords(text, _ROLE_HINTS))
    # общие роли оставляем, только если нет более точных
    if len(roles) > 1:
        roles = [r for r in roles if r not in ("разработчик", "developer", "инженер")] or roles
    soft = list(find_keywords(text, _SOFT_HINTS))
    keywords = load_keywords()
    claimed: list[ResumeClaimedNode] = []
    for nodes in keywords.specializations.values():
        for match in match_nodes(text, nodes):
            claimed.append(
                ResumeClaimedNode(
                    id=match.node_id,
                    level=2 if match.hits >= 2 else 1,
                    evidence="ключевые слова: " + ", ".join(match.keywords),
                )
            )
    claimed.sort(key=lambda n: (-n.level, n.id))
    return ResumeFacts(
        stack=detect_stack_tags(text, keywords.stack_tags),
        roles=roles[:6],
        years=years,
        soft_skills=soft[:6],
        claimed_nodes=claimed[:8],
    )


def stub_answer_bank(variables: dict[str, Any]) -> AnswerBank:
    variant = _as_dict(variables.get("variant"))
    reference = str(variant.get("reference_answer") or "эталонный ответ")
    rubric = variables.get("rubric") or []
    texts = [str(r.get("text", "")) for r in rubric if isinstance(r, dict)]
    half = texts[: max(1, len(texts) // 2)]
    return AnswerBank(
        strong=f"Я бы поступил так: {reference}",
        partial="Думаю, главное здесь — " + ("; ".join(half) if half else reference[:120]) + ".",
        weak="Сделал бы как обычно, по накатанной схеме: главное — быстро закрыть задачу.",
    )


def stub_graph_summary(variables: dict[str, Any]) -> SummaryText:
    """Детерминированная сводка по графу: те же 3 абзаца, что и rules-фолбэк эндпоинта."""
    nodes = [n for n in (variables.get("nodes") or []) if isinstance(n, dict)]

    def listing(selected: list[dict[str, Any]]) -> str:
        return ", ".join(f"«{n.get('title') or n.get('id')}»" for n in selected[:5])

    fresh = [n for n in nodes if n.get("state") == "fresh"]
    fading = [n for n in nodes if n.get("state") in ("fading", "expired")]
    to_grow = [n for n in nodes if n.get("next_grade_target") and n.get("state") != "fresh"]
    paragraphs = [
        f"Сильная зона: {listing(fresh)} — узлы подтверждены и свежи."
        if fresh
        else "Свежих подтверждённых узлов пока нет — сильная зона ещё не сформирована.",
        f"Затухает или не подтверждено: {listing(fading)} — без нового подтверждения "
        "эти узлы перестанут учитываться в подборе."
        if fading
        else "Затухающих узлов нет: подтверждения свежие.",
        f"В первую очередь подтяните: {listing(to_grow)} — эти узлы входят в профиль "
        "следующего грейда."
        if to_grow
        else "Все узлы целевого профиля подтверждены — поддерживайте свежесть подтверждений.",
    ]
    return SummaryText(text="\n\n".join(paragraphs))


def stub_selection_summary(variables: dict[str, Any]) -> SummaryText:
    """Детерминированная картина по кандидатам: статистика выдачи, как в rules-фолбэке."""
    candidates = [c for c in (variables.get("candidates") or []) if isinstance(c, dict)]
    need = variables.get("need") if isinstance(variables.get("need"), dict) else {}
    has_must = any(
        isinstance(n, dict) and n.get("importance") == "must" for n in (need.get("nodes") or [])
    )
    total = len(candidates)
    if total == 0:
        return SummaryText(
            text="Кандидатов в выдаче нет — недобор: стоит понизить часть must-узлов до "
            "should или подождать новых подтверждённых профилей."
        )
    full_must = sum(1 for c in candidates if not c.get("must_missing"))
    missing_counts: dict[str, int] = {}
    for candidate in candidates:
        for title in candidate.get("must_missing") or []:
            missing_counts[str(title)] = missing_counts.get(str(title), 0) + 1
    p1 = f"В выдаче {total} кандидатов; все must-узлы закрывают {full_must} из них."
    if missing_counts:
        deficit = max(sorted(missing_counts), key=lambda title: missing_counts[title])
        p2 = (
            f"Самый дефицитный узел — «{deficit}»: его не закрывают "
            f"{missing_counts[deficit]} из {total} кандидатов — кандидат на компромисс "
            "(понизить с must до should)."
        )
    elif has_must:
        p2 = "Все must-узлы уверенно закрыты — явного дефицита нет."
    else:
        p2 = "У потребности нет must-узлов — покрытие считается по should/nice."
    p3 = (
        "Недобор: полностью требования не закрывает никто — смотреть стоит сегмент "
        "с максимальным покрытием."
        if full_must == 0
        else "Начните с кандидатов, закрывающих все must-узлы; остальные — резервный сегмент."
    )
    return SummaryText(text="\n\n".join([p1, p2, p3]))


FIXTURES = {
    "need_parse": stub_need_parse,
    "variant_generate": stub_variant_generate,
    "variant_judge": stub_variant_judge,
    "answer_grade": stub_answer_grade,
    "scenario_personal": stub_scenario_personal,
    "resume_parse": stub_resume_parse,
    "answer_bank": stub_answer_bank,
    "graph_summary": stub_graph_summary,
    "selection_summary": stub_selection_summary,
}


class StubProvider(LLMProvider):
    """`LLM_PROVIDER=stub` и последнее звено любой цепочки фолбэка."""

    name = "stub"

    def model_for(self, tier: str) -> str:
        return STUB_MODEL

    def supports(self, prompt_name: str) -> bool:
        return prompt_name in FIXTURES

    async def _complete(self, messages, *, temperature, schema, model) -> RawCompletion:
        raise NotImplementedError("StubProvider строит ответ из переменных, не из сообщений")

    async def complete(
        self,
        prompt: PromptSpec,
        variables: dict[str, Any],
        *,
        schema: type[BaseModel] | None = None,
        temperature: float | None = None,
    ) -> CompletionResult:
        fixture = FIXTURES.get(prompt.name)
        if fixture is None:
            raise LLMUnsupportedError(f"stub: нет фикстуры для промпта {prompt.name}")
        started = time.perf_counter()
        schema = schema or prompt.schema
        messages = build_messages(prompt, variables, schema)  # проверяет переменные промпта
        payload = fixture(variables)
        parsed = schema.model_validate(payload.model_dump())
        raw_text = parsed.model_dump_json()
        tokens_in = sum(len(m.content) for m in messages) // 4
        return CompletionResult(
            parsed=parsed,
            provider=self.name,
            model=STUB_MODEL,
            prompt=prompt,
            attempts=1,
            tokens_in=tokens_in,
            tokens_out=len(raw_text) // 4,
            latency_ms=int((time.perf_counter() - started) * 1000),
            raw_text=raw_text,
        )


__all__ = [
    "FIXTURES",
    "FLAG_EMPTY",
    "FLAG_HEURISTIC",
    "FLAG_TOO_CLOSE",
    "FLAG_TOO_SHORT",
    "STUB_MODEL",
    "StubProvider",
    "grade_by_keywords",
    "stub_answer_bank",
    "stub_answer_grade",
    "stub_need_parse",
    "stub_resume_parse",
    "stub_scenario_personal",
    "stub_variant_generate",
    "stub_variant_judge",
]
