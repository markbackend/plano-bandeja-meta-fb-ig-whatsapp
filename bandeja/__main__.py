"""Arranca la bandeja con la configuración del .env:  python -m bandeja"""

import logging
import os
import sys

import uvicorn

from .app import crear_app
from .config import Config, ErrorConfig


def configurar_logs() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # no mostrar URLs con tokens


def main() -> None:
    configurar_logs()
    try:
        config = Config.desde_entorno()
    except ErrorConfig as error:
        logging.getLogger("bandeja").error("%s", error)
        sys.exit(1)
    uvicorn.run(crear_app(config), host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PUERTO", "8000")), log_level="info")


if __name__ == "__main__":
    main()
