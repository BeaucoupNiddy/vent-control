"""Run with: python -m ventctl  (env: VENTCTL_HOST, VENTCTL_PORT, VENTCTL_DB, VENTCTL_DEMO_SPEED)"""

import logging
import os

import uvicorn

from .app import create_app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    host = os.environ.get("VENTCTL_HOST", "0.0.0.0")
    port = int(os.environ.get("VENTCTL_PORT", "8000"))
    uvicorn.run(create_app(), host=host, port=port)


if __name__ == "__main__":
    main()
