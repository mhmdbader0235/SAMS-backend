"""Structured logging and request correlation.

Replaces the bare `print()` calls scattered through app/main.py (startup,
shutdown, scheduler errors) with real logging, and gives every request a
correlation id that ties its log lines together and is echoed back to the
caller so a support conversation can reference one concrete request.

Dependency-light on purpose: stdlib `logging` + a hand-rolled JSON formatter.
No structlog, no external log shipper -- appropriate for a 5-container +
host-uvicorn stack that has no log aggregation today. Metrics/tracing are a
separate, larger concern and are not part of this module.
"""

import json
import logging
import time
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def get_correlation_id() -> str:
    """The current request's correlation id, or '-' outside a request
    (e.g. a background task). Used by global exception handlers to attach
    the id to an error response body."""
    return _correlation_id.get()


class _CorrelationIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
        }
        for extra_key in ("tenant_id", "user_id", "route", "status", "duration_ms"):
            value = getattr(record, extra_key, None)
            if value is not None:
                payload[extra_key] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    """Call once at process startup. Idempotent -- safe to call again (e.g.
    from a test) without duplicating handlers."""
    root = logging.getLogger()
    root.setLevel(level)

    correlation_filter = _CorrelationIdFilter()
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        handler.addFilter(correlation_filter)
        root.addHandler(handler)
    else:
        for handler in root.handlers:
            if not any(isinstance(f, _CorrelationIdFilter) for f in handler.filters):
                handler.addFilter(correlation_filter)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Reads X-Request-ID if the gateway ever sets one (it does not today --
    see back/gateway/nginx.conf), else generates one. Echoes it on the
    response and logs one line per request keyed by route *template*, not
    the raw path -- a raw path can carry a student id."""

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-ID")
        correlation_id = incoming if incoming else str(uuid4())
        token = _correlation_id.set(correlation_id)
        logger = logging.getLogger("app.request")
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.monotonic() - start) * 1000, 1)
            logger.exception(
                "unhandled exception",
                extra={
                    "route": _route_template(request),
                    "status": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            _correlation_id.reset(token)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        logger.info(
            "request",
            extra={
                "route": _route_template(request),
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-ID"] = correlation_id
        return response


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    return request.url.path
