"""Единственный источник истины по `permissions` для `GET /auth/me`.

Ролевая модель проекта — три роли (`candidate`, `employer`, `admin`); роль хранится в БД
и выбирается при первом входе. Фронт скрывает разделы по этим строкам, backend всё равно
проверяет роли на каждом эндпоинте (`require_roles`). Без роли у пользователя есть
только базовые права: увидеть себя, выбрать роль, дать согласие, удалить аккаунт.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.core.security import ROLE_ADMIN, ROLE_CANDIDATE, ROLE_EMPLOYER

# права любого аутентифицированного пользователя (до выбора роли)
BASE_PERMISSIONS: frozenset[str] = frozenset(
    {
        "auth:me",
        "auth:choose_role",
        "auth:consent",
        "auth:delete_account",
        "skills:read",  # справочник и граф навыков публичны для вошедших
    }
)

_CANDIDATE: frozenset[str] = BASE_PERMISSIONS | frozenset(
    {
        "candidates:self",  # профиль, приватность, путь до категории
        "candidates:survey",
        "candidates:accomplishments",
        "candidates:resume",
        "tests:run",  # сессии теста и eligibility
        "fsp:link",
        "invitations:respond",
        "applications:write",
        "regular_tasks:answer",
    }
)

_EMPLOYER: frozenset[str] = BASE_PERMISSIONS | frozenset(
    {
        "employers:company",
        "needs:write",
        "needs:search",
        "candidates:search",  # банк и карточки — только по псевдонимам
        "candidates:card",
        "invitations:send",
        "contacts:reveal",  # только при contact_grant — проверяется сервисом
        "vacancies:write",
        "applications:review",
        "regular_tasks:write",
    }
)

_ADMIN: frozenset[str] = (
    _CANDIDATE
    | _EMPLOYER
    | frozenset(
        {
            "admin:stats",
            "admin:llm_usage",
            "admin:demo_reset",
            "admin:pairs",
        }
    )
)

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    ROLE_CANDIDATE: _CANDIDATE,
    ROLE_EMPLOYER: _EMPLOYER,
    ROLE_ADMIN: _ADMIN,
}


def permissions_for_roles(roles: Iterable[str]) -> list[str]:
    """Базовые права ∪ права по всем ролям пользователя, отсортированный список."""
    result: set[str] = set(BASE_PERMISSIONS)
    for role in roles:
        result |= ROLE_PERMISSIONS.get(role, frozenset())
    return sorted(result)
