from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from nova.schemas.api import ErrorResponse

INVALID_INPUT = "INVALID_INPUT"
NOT_FOUND = "NOT_FOUND"
VALIDATION_ERROR = "VALIDATION_ERROR"
INTERNAL_ERROR = "INTERNAL_ERROR"
UPSTREAM_LLM_UNAVAILABLE = "UPSTREAM_LLM_UNAVAILABLE"


class ApiError(HTTPException):
    def __init__(self, *, status_code: int, error_code: str, message: str) -> None:
        super().__init__(
            status_code=status_code,
            detail={"error_code": error_code, "message": message},
        )


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        return error_response(
            status_code=exc.status_code,
            error_code=detail.get("error_code", INTERNAL_ERROR),
            message=detail.get("message", "Request failed"),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        return error_response(
            status_code=422,
            error_code=VALIDATION_ERROR,
            message="Request validation failed",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(Exception)
    async def handle_internal_error(request: Request, exc: Exception) -> JSONResponse:
        return error_response(
            status_code=500,
            error_code=INTERNAL_ERROR,
            message="Internal server error",
        )


def error_response(
    *,
    status_code: int,
    error_code: str,
    message: str,
    details: dict | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(
            error_code=error_code,
            message=message,
            details=details,
        ).model_dump(mode="json"),
    )
