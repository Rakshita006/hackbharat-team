# Trace utilities to register log filters and correlation ID middlewares.

import logging
import sys
import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request

# Thread-safe request ID context
correlation_id_ctx: ContextVar[str] = ContextVar("correlation_id", default="-")


class CorrelationIDFilter(logging.Filter):
    # Injects the active context's request ID into log records
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = correlation_id_ctx.get()
        return True


class CorrelationIDMiddleware(BaseHTTPMiddleware):
    # Starlette middleware attaching unique request correlation IDs to requests and response headers
    async def dispatch(self, request: Request, call_next):
        corr_id = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:8]
        token = correlation_id_ctx.set(corr_id)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = corr_id
            return response
        finally:
            correlation_id_ctx.reset(token)


def configure_logging():
    # Configures base logging formats to output context correlation IDs
    log_format = "%(asctime)s | %(levelname)-7s | [%(correlation_id)s] | %(name)s | %(message)s"
    date_format = "%H:%M:%S"

    cid_filter = CorrelationIDFilter()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)

    # Re-apply handlers to lock in formatting
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(fmt=log_format, datefmt=date_format))
    handler.addFilter(cid_filter)
    root_logger.addHandler(handler)

    # Register filters across core app loggers
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error", "jalsense"):
        logging.getLogger(logger_name).addFilter(cid_filter)
