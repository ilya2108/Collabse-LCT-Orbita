"""Все модели SQLAlchemy (PLAN.md §7.4). Импорт пакета регистрирует таблицы в `Base.metadata`
— alembic/env.py и тесты импортируют именно `app.models`.

Домены: `user` (зеркало Keycloak и согласия), `candidate` (профиль с шифрованными ПДн,
опрос, свершения, ФСП, подтверждения, снимок силы, категория), `skills` (справочник графа),
`assessment` (шаблоны, пул, сессии, задания, история грейда), `employer` (компания,
потребности, вакансии, приглашения, отклики, контакты, регулярные задания), `review`
(инструмент проверки жюри), `service`/`llm`/`audit` (служебные).
"""

from app.models.assessment import (
    GradeHistory,
    TaskTemplate,
    TaskVariant,
    TestItem,
    TestSession,
    TestSessionEvent,
    TestSessionIntegrity,
)
from app.models.audit import AuditLog
from app.models.base import Base
from app.models.candidate import (
    AchievementClaim,
    CandidateCategory,
    CandidateProfile,
    CandidateSkillState,
    FspAchievement,
    SkillConfirmation,
    SurveyResponse,
)
from app.models.employer import (
    Application,
    Company,
    CompanyMember,
    ContactGrant,
    ContactRevealAudit,
    EmployerNeed,
    Invitation,
    NeedPin,
    NeedPipelineEntry,
    NeedVersion,
    PipelineStageEvent,
    RegularTask,
    RegularTaskAssignment,
    Vacancy,
)
from app.models.llm import LlmCall
from app.models.review import ImportedCandidate, PairRun
from app.models.service import ValidationRun
from app.models.skills import (
    FspDisciplineMap,
    GradeProfile,
    Industry,
    SkillEdge,
    SkillNode,
    Specialization,
)
from app.models.tgbot import TgLink, TgLinkToken, TgOutboxMessage
from app.models.user import AppUser

__all__ = [
    "Base",
    # пользователи и ПДн
    "AppUser",
    "CandidateProfile",
    "Company",
    # справочник
    "Industry",
    "Specialization",
    "SkillNode",
    "SkillEdge",
    "GradeProfile",
    "FspDisciplineMap",
    # кандидат
    "SurveyResponse",
    "AchievementClaim",
    "FspAchievement",
    "SkillConfirmation",
    "CandidateSkillState",
    "CandidateCategory",
    # тестирование
    "TaskTemplate",
    "TaskVariant",
    "TestSession",
    "TestItem",
    "TestSessionEvent",
    "TestSessionIntegrity",
    "GradeHistory",
    # работодатель и подбор
    "CompanyMember",
    "EmployerNeed",
    "NeedVersion",
    "NeedPin",
    "NeedPipelineEntry",
    "PipelineStageEvent",
    "Vacancy",
    "Invitation",
    "Application",
    "ContactGrant",
    "ContactRevealAudit",
    "RegularTask",
    "RegularTaskAssignment",
    # телеграм-бот
    "TgLink",
    "TgLinkToken",
    "TgOutboxMessage",
    # инструмент проверки жюри
    "PairRun",
    "ImportedCandidate",
    # служебные
    "LlmCall",
    "ValidationRun",
    "AuditLog",
]
