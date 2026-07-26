"""Small ASGI security controls that do not depend on a specific proxy."""

from time import monotonic

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from openenterprise_twin.api.observability import OperationalMetrics

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": (
        "camera=(), geolocation=(), microphone=(), payment=(), usb=()"
    ),
    "Cache-Control": "no-store",
}
_KNOWN_HTTP_METHODS = frozenset(
    {"DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"}
)


class RequestBodyTooLargeError(Exception):
    """Raised internally when a streaming request crosses the configured limit."""


class OperationalMetricsMiddleware:
    """Record completed HTTP requests using registered routes only."""

    def __init__(self, app: ASGIApp, *, metrics: OperationalMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started_at = monotonic()
        status_code: int | None = None

        async def record_response(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, record_response)
        finally:
            if status_code is not None:
                self.metrics.record_http(
                    method=_metric_method(scope["method"]),
                    route=_metric_route(scope),
                    status_code=status_code,
                    duration_seconds=monotonic() - started_at,
                )


class SecurityHeadersMiddleware:
    """Apply baseline API response headers to every HTTP response."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def apply_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
            await send(message)

        await self.app(scope, receive, apply_headers)


def _metric_method(method: str) -> str:
    return method if method in _KNOWN_HTTP_METHODS else "OTHER"


def _metric_route(scope: Scope) -> str:
    route = scope.get("route")
    route_path = getattr(route, "path", None)
    return route_path if isinstance(route_path, str) else "unmatched"


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies, including chunked requests."""

    def __init__(self, app: ASGIApp, *, max_body_bytes: int) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = Headers(scope=scope).get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.max_body_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return

        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > self.max_body_bytes:
                    raise RequestBodyTooLargeError
            return message

        try:
            await self.app(scope, limited_receive, send)
        except RequestBodyTooLargeError:
            await self._reject(scope, receive, send)

    async def _reject(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        trace_id = scope.get("state", {}).get(
            "trace_id", "request-rejected-at-edge"
        )
        response = JSONResponse(
            status_code=413,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": "Request body is too large",
                "status": 413,
                "code": "request_body_too_large",
                "detail": (
                    f"Request bodies must not exceed {self.max_body_bytes} bytes."
                ),
                "trace_id": trace_id,
                "violations": [],
            },
        )
        await response(scope, receive, send)
