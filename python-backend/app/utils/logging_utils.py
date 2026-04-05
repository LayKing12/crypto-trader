"""Structured logging — every decision is traceable."""
import logging
import structlog
from app.config import get_settings

settings = get_settings()


def configure_logging():
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if settings.app_env == "development"
            else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


def get_logger(name: str):
    return structlog.get_logger(name)


def log_decision(logger, symbol: str, inputs: dict, scores: dict, claude_output: dict, decision: str):
    """Mandatory decision log — every trade decision must call this."""
    logger.info(
        "signal_decision",
        symbol=symbol,
        inputs=inputs,
        scores=scores,
        claude_output=claude_output,
        final_decision=decision,
    )
