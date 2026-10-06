"""LLM-конвейер (PLAN.md §6): провайдеры (gigachat / yandex / replay / rules / stub),
версионируемые промпты, строгий JSON по Pydantic-схемам, санитайзер ПДн, журнал `llm_call`.

Точка входа для других модулей — `app.modules.llm.service`:

    from app.modules.llm.service import get_llm_service, to_api_error
    from app.modules.llm.schemas import NeedProfile

    profile = await get_llm_service().complete_json(
        "need_parse",
        {"need_text": text, "specializations": [...], "grades": [...], "catalog": [...]},
        session=session,
    )

Ошибки — `app.modules.llm.base.LLMError` (и подклассы); в API — `to_api_error(exc)`.
"""
