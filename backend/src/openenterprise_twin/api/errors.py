"""Stable RFC 9457-style problem details for API failures."""

import logging
import re
from collections.abc import Awaitable, Callable
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.responses import Response

from openenterprise_twin.api.schemas import FieldViolation, ProblemDetail

_LOGGER = logging.getLogger(__name__)
_TRACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class ApiProblemError(Exception):
    def __init__(
        self,
        *,
        status: int,
        code: str,
        title: str,
        detail: str,
        violations: tuple[FieldViolation, ...] = (),
    ) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.violations = violations


def install_error_handlers(app: FastAPI) -> None:
    @app.middleware("http")
    async def attach_trace_id(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        supplied_trace_id = request.headers.get("X-Trace-ID", "")
        request.state.trace_id = (
            supplied_trace_id
            if _TRACE_ID_PATTERN.fullmatch(supplied_trace_id)
            else uuid4().hex
        )
        response = await call_next(request)
        response.headers["X-Trace-ID"] = request.state.trace_id
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            principal = getattr(request.state, "principal", None)
            audit_path = request.url.path.encode("unicode_escape").decode("ascii")
            _LOGGER.info(
                "audit_event method=%s path=%s status=%s subject=%s trace_id=%s",
                request.method,
                audit_path,
                response.status_code,
                getattr(principal, "subject", "unauthenticated"),
                request.state.trace_id,
            )
        return response

    @app.exception_handler(ApiProblemError)
    async def handle_api_problem(
        request: Request,
        error: ApiProblemError,
    ) -> JSONResponse:
        return _problem_response(
            request,
            status=error.status,
            code=error.code,
            title=error.title,
            detail=error.detail,
            violations=error.violations,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        error: RequestValidationError,
    ) -> JSONResponse:
        violations = tuple(
            FieldViolation(
                field=".".join(str(part) for part in item["loc"] if part != "body"),
                message=str(item["msg"]),
            )
            for item in error.errors()
        )
        return _problem_response(
            request,
            status=422,
            code="request_validation",
            title="Request validation failed",
            detail="One or more request fields are invalid.",
            violations=violations,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request,
        error: Exception,
    ) -> JSONResponse:
        _LOGGER.exception(
            "unexpected API error trace_id=%s",
            request.state.trace_id,
            exc_info=error,
        )
        return _problem_response(
            request,
            status=500,
            code="internal_error",
            title="Internal server error",
            detail="The request could not be completed.",
        )


def _problem_response(
    request: Request,
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    violations: tuple[FieldViolation, ...] = (),
) -> JSONResponse:
    problem = ProblemDetail(
        title=title,
        status=status,
        code=code,
        detail=detail,
        trace_id=request.state.trace_id,
        violations=violations,
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )
