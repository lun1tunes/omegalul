"""``python -m app`` — host/port come from ClusterSettings, not from a fragile .bat parser."""

from __future__ import annotations

import logging

import uvicorn

from .settings import ClusterSettings, LOADED_ENV_FILES

logger = logging.getLogger("tnav-cluster")


def main() -> None:
    settings = ClusterSettings.from_env()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logger.info(
        "tNav Cluster Agent %s:%s ready=%s env_files=%s",
        settings.listen_host,
        settings.listen_port,
        settings.ready,
        [str(path) for path in LOADED_ENV_FILES] or ["<none>"],
    )
    uvicorn.run("app.main:app", host=settings.listen_host, port=settings.listen_port)


if __name__ == "__main__":
    main()
