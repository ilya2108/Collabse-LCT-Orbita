"""Единый формат ошибок API и обработчики исключений.

Контракт (PLAN.md §7.5): плоское тело `{"code", "message", "details", "request_id"}`;
`message` — человекочитаемый, на русском; `code` — стабильный машинный;
`request_id` = значение заголовка X-Request-Id (входящего, если он похож на идентификатор,
иначе сгенерированного). Id живёт в `request.state` (общий scope запроса) и в ContextVar
для логов; необработанные исключения ловятся в `RequestIdMiddleware`, поэтому 500-ответ
тоже несёт `request_id` и заголовок, а внешние middleware (CORS) видят обычный ответ.
"""

from __future__ import annotations

import logging
import re
from contextvars import ContextVar
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger("app.errors")

REQUEST_ID_HEADER = "X-Request-Id"
# входящий X-Request-Id принимается только в таком виде; иначе генерируется uuid
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

# request_id текущего запроса; проставляется middleware, читается обработчиками и логами
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def current_request_id(request: Request | None = None) -> str:
    """Id запроса: из `request.state` (переживает выход из middleware), иначе из ContextVar."""
    if request is not None:
        rid = getattr(request.state, "request_id", None)
        if rid:
            return str(rid)
    return request_id_var.get()


# стабильные машинные коды для «голых» HTTPException по статусу
_STATUS_CODES: dict[int, str] = {
    400: "validation_error",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "file_too_large",
    422: "unprocessable",
    429: "rate_limited",
    500: "internal_error",
    501: "not_implemented",
    502: "integration_unavailable",
    503: "service_unavailable",
}

_STATUS_MESSAGES: dict[int, str] = {
    400: "Ошибка валидации данных",
    401: "Требуется аутентификация",
    403: "Недостаточно прав для выполнения операции",
    404: "Объект не найден",
    405: "Метод не поддерживается",
    409: "Конфликт данных",
    413: "Файл превышает допустимый размер",
    422: "Запрос не может быть обработан",
    429: "Слишком много запросов, повторите позже",
    500: "Внутренняя ошибка сервера",
    501: "Метод ещё не реализован",
    502: "Внешний сервис недоступен",
    503: "Сервис временно недоступен",
}


class ApiError(Exception):
    """Доменная ошибка с телом по контракту."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


# --- фабрики частых ошибок --------------------------------------------------------------


def unauthorized(message: str = "Требуется аутентификация") -> ApiError:
    return ApiError(401, "unauthorized", message)


def forbidden(message: str = "Недостаточно прав для выполнения операции") -> ApiError:
    return ApiError(403, "forbidden", message)


def not_found(message: str = "Объект не найден") -> ApiError:
    return ApiError(404, "not_found", message)


def conflict(message: str = "Конфликт данных", code: str = "conflict") -> ApiError:
    """409 с машинным кодом по умолчанию `conflict`; доменные коды передаются явно
    (например, `conflict("Роль уже выбрана", code="role_already_set")`)."""
    return ApiError(409, code, message)


def stale_version(
    message: str = "Объект был изменён другим пользователем, обновите данные",
) -> ApiError:
    return ApiError(409, "stale_version", message)


def validation_error(
    message: str = "Ошибка валидации данных", details: list[dict[str, Any]] | None = None
) -> ApiError:
    return ApiError(400, "validation_error", message, details)


def unprocessable(message: str = "Запрос не может быть обработан") -> ApiError:
    return ApiError(422, "unprocessable", message)


def rate_limited(
    message: str = "Слишком много запросов, повторите позже",
    retry_after_seconds: int | None = None,
) -> ApiError:
    headers = {"Retry-After": str(retry_after_seconds)} if retry_after_seconds else None
    return ApiError(429, "rate_limited", message, headers=headers)


def not_implemented(
    message: str = "Функциональность в разработке и появится в следующей версии",
) -> ApiError:
    return ApiError(501, "not_implemented", message)


def integration_unavailable(message: str = "Внешний сервис недоступен") -> ApiError:
    """502 для сбоев провайдера LLM / реестра ФСП, когда фолбэк невозможен."""
    return ApiError(502, "integration_unavailable", message)


# --- построение ответа --------------------------------------------------------------------


def error_payload(
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "details": details or [],
        "request_id": request_id or request_id_var.get() or None,
    }


def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
    request: Request | None = None,
) -> JSONResponse:
    all_headers = dict(headers or {})
    rid = current_request_id(request)
    if rid:
        all_headers[REQUEST_ID_HEADER] = rid
    return JSONResponse(
        status_code=status_code,
        content=error_payload(code, message, details, rid),
        headers=all_headers,
    )


def incoming_request_id(raw: str | None) -> str:
    """Клиентский X-Request-Id, если он похож на идентификатор, иначе новый uuid."""
    if raw and REQUEST_ID_RE.match(raw):
        return raw
    return str(uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Проставляет X-Request-Id в `request.state`, ContextVar и ответ; необработанное
    исключение превращает в 500-конверт здесь же (внешний ServerErrorMiddleware вызывает
    обработчики уже после сброса ContextVar и вне CORS)."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        rid = incoming_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = rid
        token = request_id_var.set(rid)
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 — последний рубеж: любой сбой → 500 по контракту
            response = await _handle_unexpected(request, exc)
        finally:
            request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response


# --- обработчики -----------------------------------------------------------------------------


async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    return _error_response(
        exc.status_code, exc.code, exc.message, exc.details, exc.headers, request=request
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "field": ".".join(str(part) for part in err.get("loc", []) if part != "body"),
            "code": err.get("type", "invalid"),
            "message": err.get("msg", ""),
        }
        for err in exc.errors()
    ]
    # контракт: ошибки валидации тела/параметров — 400 validation_error (не 422 FastAPI)
    return _error_response(
        400, "validation_error", "Ошибка валидации данных", details, request=request
    )


async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODES.get(exc.status_code, "http_error")
    message = (
        exc.detail
        if isinstance(exc.detail, str) and exc.detail not in ("", "Not Found")
        else _STATUS_MESSAGES.get(exc.status_code, "Ошибка обработки запроса")
    )
    return _error_response(
        exc.status_code, code, message, headers=dict(exc.headers or {}), request=request
    )


async def _handle_stale_data(request: Request, exc: StaleDataError) -> JSONResponse:
    return _error_response(
        409,
        "stale_version",
        "Объект был изменён другим пользователем, обновите данные",
        request=request,
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # детали — только в логах (с request_id для поиска), наружу — стабильный internal_error
    # без текста исключения; параметры SQL в исключениях скрыты (db: hide_parameters)
    logger.exception(
        "Необработанная ошибка [%s]: %s %s",
        current_request_id(request),
        request.method,
        request.url.path,
    )
    return _error_response(500, "internal_error", "Внутренняя ошибка сервера", request=request)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _handle_api_error)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(StaleDataError, _handle_stale_data)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _handle_unexpected)
