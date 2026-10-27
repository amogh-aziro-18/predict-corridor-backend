"""Map application errors to HTTP responses."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import (
    AppError,
    EnvironmentUnavailableError,
    NotFoundError,
    ValidationAppError,
    WeatherUnavailableError,
)


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": exc.message, "code": exc.code})

    @app.exception_handler(ValidationAppError)
    async def validation_handler(_: Request, exc: ValidationAppError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": exc.message, "code": exc.code})

    @app.exception_handler(WeatherUnavailableError)
    async def weather_unavailable_handler(_: Request, exc: WeatherUnavailableError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(EnvironmentUnavailableError)
    async def environment_unavailable_handler(
        _: Request, exc: EnvironmentUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": exc.message, "code": exc.code},
        )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.message, "code": exc.code})
