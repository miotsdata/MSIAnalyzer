from datetime import datetime
import logging
from pathlib import Path


class DefaultSourceFileFilter(logging.Filter):
    """
    Add source file attribute if not present in log
    record.
    """

    def filter(self, record):
        """Ensure every record has a `source_file` attribute.

        Args:
            record: The log record being processed.

        Returns:
            True, so the record is never dropped.
        """
        if not hasattr(record, "source_file"):
            record.source_file = "-"
        return True


def configure_logging(
    *,
    level: int = logging.INFO,
    log_file: Path | None = None,
    debug_log_dir: Path | None = None,
) -> None:
    """
    Configure logging.

    Sets up two handlers:
      - a console handler, emitting `level` and above with a concise format.
      - a debug file handler, always emitting DEBUG and above with a
        verbose, source-annotated format, written to a timestamped file
        under `debug_log_dir`.

    An optional additional file handler can be attached via `log_file`,
    using the same level and format as the console handler.

    Args:
        level: Console (and optional log_file) logging level. Defaults to logging.INFO.
        log_file: Optional path to an additional file handler at `level`. Defaults to None.
        debug_log_dir: Directory for the timestamped debug log file.
            Defaults to <src/msianalyzer>/log.

    Returns:
        None
    """
    handlers = []

    concise_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] (%(source_file)s) %(message)s"
    )
    verbose_formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s.%(funcName)s (%(source_file)s): %(message)s"
    )

    # Console handler: `level` and above, concise format
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(concise_formatter)
    console_handler.addFilter(DefaultSourceFileFilter())
    handlers.append(console_handler)

    # Debug file handler: always DEBUG+, verbose format, timestamped filename
    if debug_log_dir is None:
        # this file: src/msianalyzer/core/utils/logging_utils.py
        # parents[2] -> src/msianalyzer
        debug_log_dir = Path(__file__).resolve().parents[2] / "log"
    debug_log_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_path = debug_log_dir / f"debug_{timestamp}.log"

    debug_handler = logging.FileHandler(debug_log_path)
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(verbose_formatter)
    debug_handler.addFilter(DefaultSourceFileFilter())
    handlers.append(debug_handler)

    # Optional extra file handler at `level`, concise format
    if log_file is not None:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(concise_formatter)
        file_handler.addFilter(DefaultSourceFileFilter())
        handlers.append(file_handler)

    logging.basicConfig(
        level=logging.DEBUG,
        handlers=handlers,
        force=True,
    )
