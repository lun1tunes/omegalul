"""``python -m app`` — host/port come from Settings, not from a fragile .bat parser."""

from __future__ import annotations

import logging

import uvicorn

from app.settings import configure_logging, get_settings

logger = logging.getLogger("mas-activity")


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    summary = settings.public_summary()
    logger.info(
        "MAS Activity %s:%s transport=%s env_files=%s",
        settings.host,
        settings.port,
        summary["n8n_transport"],
        summary["env_files"] or ["<none>"],
    )
    if settings.n8n_transport == "unconfigured":
        logger.error("%s", "n8n is not configured — /ready will fail until webhook or N8N_BASE_URL is set")
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
