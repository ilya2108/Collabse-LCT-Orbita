"""Модель данных PLAN.md §7.4: все модели импортируются, метаданные содержат ожидаемые
таблицы, инварианты уровня БД и паттерн шифрования ПДн заданы, миграция 0001 им соответствует."""

from __future__ import annotations

import re
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.orm import configure_mappers

import app.models as models
from app.core.pii import EncryptedStr
from app.models.base import Base
from app.tools.rekey import encrypted_targets

EXPECTED_TABLES = {
    # пользователи и ПДн
    "app_user",
    "candidate_profile",
    "company",
    # справочник
    "industry",
    "specialization",
    "skill_node",
    "skill_edge",
    "grade_profile",
    "fsp_discipline_map",
    # кандидат
    "survey_response",
    "achievement_claim",
    "fsp_achievement",
    "skill_confirmation",
    "candidate_skill_state",
    "candidate_category",
    # тестирование
    "task_template",
    "task_variant",
    "test_session",
    "test_item",
    "grade_history",
    # работодатель и подбор
    "employer_need",
    "need_version",
    "need_pin",
    "vacancy",
    "invitation",
    "application",
    "contact_grant",
    "contact_reveal_audit",
    "regular_task",
    "regular_task_assignment",
    # инструмент проверки жюри
    "pair_run",
    "imported_candidate",
    # служебные
    "llm_call",
    "validation_run",
    "audit_log",
}

# таблицы миграции 0002 (профили работодателя и мини-CRM); EXPECTED_TABLES — только 0001
TABLES_0002 = {"company_member", "need_pipeline_entry", "pipeline_stage_event"}
# таблицы миграции 0003 (телеграм-бот: привязки, одноразовые коды, журнал исходящих)
TABLES_0003 = {"tg_link", "tg_link_token", "tg_outbox"}
# таблицы миграции 0004 (сигналы целостности: телеметрия раннера и кэш агрегата)
TABLES_0004 = {"test_session_event", "test_session_integrity"}
ALL_TABLES = EXPECTED_TABLES | TABLES_0002 | TABLES_0003 | TABLES_0004

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0001_initial.py"
MIGRATION_0002 = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0002_employer_crm.py"
)
MIGRATION_0003 = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0003_telegram_bot.py"
)
MIGRATION_0004 = (
    Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0004_integrity_signals.py"
)


def _table(name: str) -> sa.Table:
    return Base.metadata.tables[name]


def _checks(table: sa.Table) -> dict[str, str]:
    """{короткое имя CHECK (без префикса naming convention `ck_<table>_`): выражение}."""
    prefix = f"ck_{table.name}_"
    return {
        str(c.name).removeprefix(prefix): str(c.sqltext)
        for c in table.constraints
        if isinstance(c, sa.CheckConstraint) and c.name
    }


def _unique_sets(table: sa.Table) -> set[tuple[str, ...]]:
    result: set[tuple[str, ...]] = set()
    for c in table.constraints:
        if isinstance(c, sa.UniqueConstraint):
            result.add(tuple(col.name for col in c.columns))
    for idx in table.indexes:
        if idx.unique:
            result.add(tuple(col.name for col in idx.columns))
    return result


def _index_sets(table: sa.Table) -> set[tuple[str, ...]]:
    return {tuple(col.name for col in idx.columns) for idx in table.indexes}


def test_all_models_import_and_configure() -> None:
    configure_mappers()
    assert set(Base.metadata.tables) == ALL_TABLES
    assert all(hasattr(models, name) for name in models.__all__)
    # каждая таблица создаётся ровно одной моделью
    assert len({m.class_.__tablename__ for m in Base.registry.mappers}) == len(ALL_TABLES)
    # граф FK ацикличен — sorted_tables не падает
    assert len(Base.metadata.sorted_tables) == len(ALL_TABLES)


def test_pii_columns_are_encrypted_with_blind_index() -> None:
    expected = {
        "app_user": ("email",),
        "candidate_profile": ("full_name", "phone", "telegram"),
        "company": ("contact_email", "contact_phone"),
        "company_member": ("full_name", "email", "telegram"),
    }
    for table_name, attrs in expected.items():
        table = _table(table_name)
        for attr in attrs:
            assert attr not in table.c, f"{table_name}.{attr}: ПДн не должны лежать открыто"
            enc = table.c[f"{attr}_enc"]
            assert isinstance(enc.type, EncryptedStr)
            hash_col = table.c[f"{attr}_hash"]
            assert isinstance(hash_col.type, sa.CHAR) and hash_col.type.length == 64
    # rekey находит все шифрованные колонки автоматически
    targets = {model.__tablename__: attrs for model, attrs in encrypted_targets()}
    assert targets == {k: tuple(v) for k, v in expected.items()}


def test_salary_invariants() -> None:
    for table_name in ("vacancy", "invitation"):
        checks = _checks(_table(table_name))
        assert "salary_range" in checks, table_name
        assert "salary_from > 0" in checks["salary_range"]
        assert "salary_from <= salary_to" in checks["salary_range"]
        table = _table(table_name)
        assert not table.c.salary_from.nullable and not table.c.salary_to.nullable
    need_checks = _checks(_table("employer_need"))
    assert "salary_from <= salary_to" in need_checks["salary_range"]
    assert "salary_floor > 0" in _checks(_table("candidate_profile"))["salary_floor_positive"]


def test_invitation_single_open_per_pair() -> None:
    table = _table("invitation")
    partial = [idx for idx in table.indexes if idx.name == "uq_invitation_open_pair"]
    assert len(partial) == 1
    idx = partial[0]
    assert idx.unique
    assert [c.name for c in idx.columns] == ["company_id", "candidate_id"]
    where = str(idx.dialect_options["postgresql"]["where"])
    assert "'sent'" in where and "'viewed'" in where
    # FK приглашения — только на candidate_profile (импортированные записи не приглашаются)
    fk_targets = {fk.column.table.name for fk in table.c.candidate_id.foreign_keys}
    assert fk_targets == {"candidate_profile"}


def test_assessment_and_matching_indexes() -> None:
    assert ("candidate_id", "variant_id") in _unique_sets(_table("test_item"))
    assert ("session_id", "position") in _unique_sets(_table("test_item"))
    assert ("candidate_id", "node_id") in _index_sets(_table("skill_confirmation"))
    assert ("specialization_code", "grade") in _index_sets(_table("candidate_category"))
    assert ("specialization_code", "grade") in _unique_sets(_table("grade_profile"))
    assert ("candidate_id", "specialization_code") in _unique_sets(_table("candidate_category"))
    state_pk = tuple(c.name for c in _table("candidate_skill_state").primary_key.columns)
    assert state_pk == ("candidate_id", "node_id")
    assert ("candidate_id", "vacancy_id") in _unique_sets(_table("application"))
    assert ("task_id", "candidate_id") in _unique_sets(_table("regular_task_assignment"))


def test_status_checks_present() -> None:
    expectations = [
        ("specialization", "status_known", "'preview'"),
        ("skill_node", "layer_known", "'D'"),
        ("skill_edge", "kind_known", "'bridge'"),
        ("skill_confirmation", "source_known", "'regular'"),
        ("test_session", "status_known", "'abandoned'"),
        ("test_session", "mode_known", "'seed'"),
        ("test_session", "outcome_known", "'strong'"),
        ("task_variant", "status_known", "'rejected'"),
        ("need_version", "mode_known", "'unverified'"),
        ("invitation", "status_known", "'expired'"),
        ("pair_run", "mode_known", "'bulk'"),
        ("llm_call", "status_known", "'fallback'"),
    ]
    for table_name, check_name, literal in expectations:
        checks = _checks(_table(table_name))
        assert check_name in checks, (table_name, check_name)
        assert literal in checks[check_name], (table_name, check_name)


def test_isolation_of_review_tables() -> None:
    """imported_candidate живёт отдельно: без FK на app_user/candidate_profile и без ссылок
    из продуктовых таблиц (приглашения, подтверждения, категории)."""
    imported = _table("imported_candidate")
    targets = {fk.column.table.name for fk in imported.foreign_keys}
    assert targets == {"pair_run"}
    referencing = {
        fk.parent.table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "imported_candidate"
    }
    assert referencing == set()


def test_migration_0001_matches_metadata() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    assert 'revision = "0001"' in source and "down_revision = None" in source
    assert "app.core" not in source, "миграция не должна импортировать код приложения"
    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert created == EXPECTED_TABLES
    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', source))
    assert dropped == EXPECTED_TABLES
    assert "uq_invitation_open_pair" in source and "postgresql_where" in source
    assert source.count("sa.LargeBinary()") == 6  # email + 3 ПДн кандидата + 2 контакта компании


def test_migration_0002_matches_metadata() -> None:
    source = MIGRATION_0002.read_text(encoding="utf-8")
    assert 'revision = "0002"' in source and 'down_revision = "0001"' in source
    assert "app.core" not in source, "миграция не должна импортировать код приложения"
    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert created == TABLES_0002
    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', source))
    assert dropped == TABLES_0002
    # company.kind добавляется и удаляется, CHECK создаётся явно
    assert 'op.add_column(\n        "company"' in source or 'op.add_column("company"' in source
    assert "ck_company_kind_known" in source
    assert 'op.drop_column("company", "kind")' in source
    # ПДн приглашённого участника — только шифртекст (3 колонки *_enc)
    assert source.count("sa.LargeBinary()") == 3
    # частичный уникальный индекс «одна строка участия пользователя в компании»
    assert "uq_company_member_company_user" in source and "user_id IS NOT NULL" in source


def test_migration_0003_matches_metadata() -> None:
    source = MIGRATION_0003.read_text(encoding="utf-8")
    assert 'revision = "0003"' in source and 'down_revision = "0002"' in source
    assert "app.core" not in source, "миграция не должна импортировать код приложения"
    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert created == TABLES_0003
    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', source))
    assert dropped == TABLES_0003
    # один чат — одна привязка; HMAC кода привязки уникален
    assert "uq_tg_link_tg_chat_id" in source
    assert "uq_tg_link_token_token_hash" in source
    # привязка указывает на пользователя ИЛИ участника компании (HR без аккаунта)
    assert "user_id IS NOT NULL OR member_id IS NOT NULL" in source
    # tg_chat_id — технический идентификатор, ПДн-шифрования в таблицах бота нет
    assert "LargeBinary" not in source


def test_migration_0004_matches_metadata() -> None:
    source = MIGRATION_0004.read_text(encoding="utf-8")
    assert 'revision = "0004"' in source and 'down_revision = "0003"' in source
    assert "app.core" not in source, "миграция не должна импортировать код приложения"
    created = set(re.findall(r'op\.create_table\(\s*"(\w+)"', source))
    assert created == TABLES_0004
    dropped = set(re.findall(r'op\.drop_table\("(\w+)"\)', source))
    assert dropped == TABLES_0004
    # телеметрия — только виды событий, длины и тайминги; ПДн-шифрования здесь нет
    assert "LargeBinary" not in source
    assert "'answer_submitted'" in source  # CHECK перечисляет виды событий
    assert "ck_test_session_integrity_level_known" in source


def test_integrity_tables_shape() -> None:
    event = _table("test_session_event")
    assert {c.name for c in event.columns} == {
        "id",
        "session_id",
        "kind",
        "client_ts",
        "received_at",
        "payload",
    }
    assert "'first_input'" in _checks(event)["kind_known"]
    assert ("session_id", "client_ts") in _index_sets(event)
    fk = {f.column.table.name for f in event.c.session_id.foreign_keys}
    assert fk == {"test_session"}

    integrity = _table("test_session_integrity")
    pk = tuple(c.name for c in integrity.primary_key.columns)
    assert pk == ("session_id",)  # одна строка кэша на сессию (upsert при пересчёте)
    assert "'low'" in _checks(integrity)["level_known"]
    assert ("candidate_id",) in _index_sets(integrity)


def test_tgbot_tables_shape() -> None:
    link = _table("tg_link")
    assert {c.name for c in link.columns} == {
        "id",
        "tg_chat_id",
        "user_id",
        "member_id",
        "role",
        "created_at",
    }
    member_fk = {fk.column.table.name for fk in link.c.member_id.foreign_keys}
    assert member_fk == {"company_member"}, "HR без аккаунта привязывается к company_member"
    token = _table("tg_link_token")
    assert isinstance(token.c.token_hash.type, sa.CHAR) and token.c.token_hash.type.length == 64
    outbox = _table("tg_outbox")
    checks = _checks(outbox)
    assert "'failed'" in checks["status_known"]
    assert ("kind", "ref_id") in _index_sets(outbox)
