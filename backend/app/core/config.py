"""Конфигурация приложения из переменных окружения.

Имена переменных — контракт команды (PLAN.md §7, CLAUDE.md «Env backend»): поля объявлены
в lower_snake_case, pydantic-settings сопоставляет их с UPPER_CASE env-переменными без
учёта регистра. Источник значений — `.env` (локально) или окружение контейнера.

Секреты в репозитории не хранятся: `.env.example` содержит только плейсхолдеры; ключей LLM
может не быть вовсе — тогда работает `LLM_PROVIDER=stub`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# общеизвестные dev-дефолты секретов: вне dev/test они запрещены
_DEV_SECRET_DEFAULTS = frozenset({"dev-internal-token"})

LLM_PROVIDERS = ("stub", "gigachat", "yandex", "replay", "rules")
APP_ENVS = ("dev", "prod", "test")
PII_HMAC_MIN_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- общее ---------------------------------------------------------------
    # только dev | prod | test: опечатка (production, Prod) не должна молча отключать
    # fail-closed проверки prod
    app_env: Literal["dev", "prod", "test"] = "dev"
    log_level: str = "INFO"
    # доверять X-Forwarded-For (последний hop) — только за известным прокси (Caddy на VPS);
    # None → выводится из app_env: в prod заголовок перезаписывает Caddy, в dev он
    # полностью подконтролен клиенту
    trusted_proxy: bool | None = None
    # публичный адрес фронта: CORS в dev-режиме и ссылки в письмах/ответах API
    frontend_public_url: str = "http://localhost:5173"

    # --- PostgreSQL -----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://fsp:fsp@localhost:5432/fsp"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # --- Keycloak ---------------------------------------------------------------
    keycloak_internal_url: str = "http://localhost:8080"  # откуда качать JWKS
    keycloak_issuer: str = "http://localhost:8080/realms/fsp"  # ожидаемый iss в JWT
    keycloak_realm: str = "fsp"
    # допустимые audience access-токена (aud или azp); Keycloak по умолчанию ставит "account"
    keycloak_audiences: str = "account,fsp-frontend"
    jwks_cache_ttl_seconds: int = 600  # кэш JWKS 10 минут
    # confidential-клиент с service-account (realm-management: manage-users, view-users) —
    # опционален; нужен только для административных операций через Admin API
    keycloak_admin_client_id: str = ""
    keycloak_admin_client_secret: str = ""

    # --- ПДн, 152-ФЗ ---------------------------------------------------------------
    # список Fernet-ключей через запятую, первый — активный (MultiFernet-ротация)
    pii_fernet_keys: str = ""
    # отдельный ключ >= 32 байт для blind-index HMAC-SHA256
    pii_hmac_key: str = ""

    # --- межсервисная аутентификация (джобы, импорт, заглушка реестра ФСП) -----------
    internal_api_token: str = "dev-internal-token"

    # --- LLM-конвейер (PLAN.md §6) --------------------------------------------------
    # stub — фикстуры и эвристики (CI, запуск без ключей); gigachat — основной провайдер;
    # yandex — резерв; replay — кэш ответов из репозитория; rules — словарь ключевых слов
    llm_provider: str = "stub"
    gigachat_credentials: str = ""  # authorization key из кабинета GigaChat API
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model_strong: str = "GigaChat-2-Pro"  # судья, грейдер, разбор потребности
    gigachat_model_fast: str = "GigaChat-2-Lite"  # генерация вариантов
    # TLS к API Сбера подписан НУЦ Минцифры: путь к PEM-бандлу (root+sub с gu-st.ru);
    # пусто — системное хранилище. Отключение проверки — только аварийно, для демо-стенда.
    gigachat_ca_bundle: str = ""
    gigachat_verify_ssl_certs: bool = True
    yc_folder_id: str = ""
    yc_api_key: str = ""
    yandex_model: str = "yandexgpt"
    llm_daily_token_cap: int = Field(default=2_000_000, ge=0)
    llm_max_concurrency: int = Field(default=4, ge=1, le=64)
    llm_timeout_seconds: float = Field(default=60.0, gt=0)

    # --- справочник навыков (YAML-графы, профили грейдов, шаблоны заданий) -------------
    # относительный путь считается от корня backend (родитель пакета app), см. skills_path
    skills_dir: str = "app/skills"

    # --- тестирование: демо-режим и ограничения (PLAN.md §3.4, §4) ----------------------
    # демо-режим: укороченная сессия для жюри и короткий cooldown вместо 90 дней
    test_demo_mode: bool = False
    demo_cooldown_seconds: int = Field(default=300, ge=0)
    grade_cooldown_days: int = Field(default=90, ge=0)

    @model_validator(mode="after")
    def _validate(self) -> Settings:
        """Fail-closed на старте, а не первым запросом:
        - ключи ПДн (PII_FERNET_KEYS — валидные Fernet-ключи, PII_HMAC_KEY ≥ 32 байт)
          обязательны в любом окружении — иначе каждый аутентифицированный запрос падал бы 500;
        - в prod общеизвестный dev-дефолт INTERNAL_API_TOKEN и пустой KEYCLOAK_AUDIENCES
          равносильны отсутствию секрета/проверки;
        - имя LLM-провайдера проверяется всегда — опечатка в env не должна молча включать stub.
        """
        if self.llm_provider not in LLM_PROVIDERS:
            raise ValueError(
                f"LLM_PROVIDER={self.llm_provider!r}: допустимые значения "
                f"{', '.join(LLM_PROVIDERS)}"
            )
        if not self.pii_fernet_key_list:
            raise ValueError(
                "PII_FERNET_KEYS не задан: сгенерируйте ключ командой "
                'python -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            )
        for index, key in enumerate(self.pii_fernet_key_list, start=1):
            try:
                Fernet(key)
            except (ValueError, TypeError) as exc:
                raise ValueError(
                    f"PII_FERNET_KEYS: ключ №{index} не является валидным Fernet-ключом "
                    "(urlsafe base64, 32 байта)"
                ) from exc
        if len(self.pii_hmac_key.encode("utf-8")) < PII_HMAC_MIN_BYTES:
            raise ValueError(
                "PII_HMAC_KEY не задан или короче 32 байт: сгенерируйте ключ командой "
                "openssl rand -base64 32"
            )
        if self.app_env == "prod":
            token = self.internal_api_token
            if not token.strip() or token in _DEV_SECRET_DEFAULTS:
                raise ValueError(
                    "APP_ENV=prod: INTERNAL_API_TOKEN пуст или равен dev-дефолту. "
                    "Сгенерируйте секрет (openssl rand -hex 32) и задайте его в окружении"
                )
            if not self.keycloak_audience_list:
                raise ValueError(
                    "APP_ENV=prod: KEYCLOAK_AUDIENCES пуст — проверка audience токена "
                    "была бы отключена; укажите client id (account,fsp-frontend)"
                )
        return self

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    @property
    def trust_forwarded_for(self) -> bool:
        if self.trusted_proxy is not None:
            return self.trusted_proxy
        return self.app_env == "prod"

    @property
    def keycloak_audience_list(self) -> list[str]:
        return [a.strip() for a in self.keycloak_audiences.split(",") if a.strip()]

    @property
    def pii_fernet_key_list(self) -> list[str]:
        return [k.strip() for k in self.pii_fernet_keys.split(",") if k.strip()]

    @property
    def jwks_url(self) -> str:
        return (
            f"{self.keycloak_internal_url.rstrip('/')}/realms/"
            f"{self.keycloak_realm}/protocol/openid-connect/certs"
        )

    @property
    def backend_root(self) -> Path:
        """Корень backend (директория с pyproject.toml) — родитель пакета `app`."""
        return Path(__file__).resolve().parents[2]

    @property
    def skills_path(self) -> Path:
        """Абсолютный путь к каталогу навыков: SKILLS_DIR как есть, если абсолютный,
        иначе — относительно корня backend (не зависит от текущей директории процесса)."""
        path = Path(self.skills_dir)
        return path if path.is_absolute() else self.backend_root / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
