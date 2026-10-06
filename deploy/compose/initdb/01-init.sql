-- Выполняется только при первом старте пустого тома PostgreSQL.
-- Отдельная БД для Keycloak в том же инстансе: fsp — приложение, keycloak — IAM.
-- Схема приложения создаётся Alembic (сервис migrate в docker-compose.dev.yml).
CREATE DATABASE keycloak;
