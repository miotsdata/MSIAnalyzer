import logging
from pathlib import Path

def configure_logging(
    *,
    level=logging.INFO,
    log_file: Path | None = None,
):
    handlers = []

    # Console handler
    console_handler = logging.StreamHandler()
    handlers.append(console_handler)

    # Optional file handler
    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        handlers.append(file_handler)

    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s.%(funcName)s: %(message)s",
        handlers=handlers,
        force=True
    )

