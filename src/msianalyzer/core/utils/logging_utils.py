from __future__ import annotations

import functools
import inspect
import logging
import time
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import QueueHandler, QueueListener
from multiprocessing import Manager
from pathlib import Path
from typing import Any, Callable, Iterator

__all__ = [
    "DefaultSourceFileFilter",
    "resolve_log_level",
    "configure_logging",
    "log_call",
    "worker_logging",
]


class DefaultSourceFileFilter(logging.Filter):
    """
    Add source file attribute if not present in log
    record.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Ensure every record has a `source_file` attribute.

        Args:
            record: The log record being processed.

        Returns:
            True, so the record is never dropped.
        """
        if not hasattr(record, "source_file"):
            record.source_file = "-"
        return True


def resolve_log_level(level: int | str) -> int:
    """Normalise a logging level given as an int or a name.

    Args:
        level: Either a numeric level (returned unchanged) or a level name
            such as ``"debug"`` / ``"INFO"`` (case-insensitive).

    Returns:
        The numeric logging level.

    Raises:
        ValueError: If ``level`` is a string that is not a known level name.
    """
    if isinstance(level, int):
        return level
    mapping = logging.getLevelNamesMapping()
    try:
        return mapping[str(level).strip().upper()]
    except KeyError as exc:
        raise ValueError(
            f"unknown log level {level!r}; expected one of "
            f"{', '.join(sorted(n for n in mapping if n.isalpha()))}"
        ) from exc


def configure_logging(
    *,
    level: int | str = logging.INFO,
    log_file: Path | None = None,
    debug_log_dir: Path | None = None,
    run_id: str | None = None,
) -> None:
    """
    Configure logging.

    Sets up:
      - a console handler, emitting `level` and above with a concise format.
      - a debug file handler, always emitting DEBUG and above with a
        verbose, source-annotated format, written to a timestamped file
        under `debug_log_dir`.
      - an optional additional file handler (`log_file`), using the same
        level and format as the console handler.

    The console handler and the optional `log_file` handler both honour
    `level`; the debug file handler is always DEBUG.

    Args:
        level: Console (and optional `log_file`) logging level, as a number
            or a name (``"debug"``, ``"info"``, ...). Defaults to INFO.
        log_file: Optional path to an additional file handler at `level`.
            Defaults to None (no user log file).
        debug_log_dir: Directory for the always-on DEBUG log file. Defaults
            to ``<src/msianalyzer>/log``.
        run_id: When given, the DEBUG log file is named
            ``debug_<run_id>.log`` instead of ``debug_<timestamp>.log``.

    Returns:
        None
    """
    level = resolve_log_level(level)
    handlers: list[logging.Handler] = []

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

    # Debug file handler: always DEBUG+, verbose format
    if debug_log_dir is None:
        # this file: src/msianalyzer/core/utils/logging_utils.py
        # parents[2] -> src/msianalyzer
        debug_log_dir = Path(__file__).resolve().parents[2] / "log"
    debug_log_dir = Path(debug_log_dir)
    debug_log_dir.mkdir(parents=True, exist_ok=True)

    stamp = run_id or datetime.now().strftime("%Y%m%d_%H%M%S")
    debug_log_path = debug_log_dir / f"debug_{stamp}.log"

    debug_handler = logging.FileHandler(debug_log_path)
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(verbose_formatter)
    debug_handler.addFilter(DefaultSourceFileFilter())
    handlers.append(debug_handler)

    # Optional extra user file handler at `level`, concise format
    if log_file is not None:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)
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


def log_call(
    fn: Callable | None = None,
    *,
    level: int = logging.DEBUG,
    source: str | None = None,
) -> Callable:
    """Log a "start" record before a call and an "end" record after it.

    The elapsed time is included on the end record; an exception is logged
    (once, with traceback) and re-raised. Records are emitted on the logger
    of the wrapped function's own module, so they follow the
    ``getLogger(__name__)`` convention.

    Args:
        fn: The function being decorated (supplied automatically when used
            without parentheses).
        level: Level for the start/end records. Defaults to DEBUG.
        source: Name of a parameter holding a path (e.g. ``"db_path"``);
            when the call supplies it, its value is put on the records'
            ``source_file`` attribute so the log's source column shows it.

    Returns:
        The decorated function (or a decorator when called with keywords).
    """

    def decorate(f: Callable) -> Callable:
        mod_logger = logging.getLogger(f.__module__)
        qualname = f.__qualname__
        sig = inspect.signature(f) if source else None

        @functools.wraps(f)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            extra = None
            if sig is not None:
                try:
                    bound = sig.bind_partial(*args, **kwargs)
                    if source in bound.arguments:
                        extra = {"source_file": str(bound.arguments[source])}
                except TypeError:
                    extra = None
            mod_logger.log(level, "start %s", qualname, extra=extra)
            t0 = time.perf_counter()
            try:
                result = f(*args, **kwargs)
            except Exception:
                mod_logger.exception(
                    "fail %s (%.1f ms)",
                    qualname,
                    (time.perf_counter() - t0) * 1e3,
                    extra=extra,
                )
                raise
            mod_logger.log(
                level,
                "end %s (%.1f ms)",
                qualname,
                (time.perf_counter() - t0) * 1e3,
                extra=extra,
            )
            return result

        return wrapper

    return decorate(fn) if fn is not None else decorate


def _worker_log_init(log_queue) -> None:
    """Worker-process initializer: forward every record to ``log_queue``.

    Each worker process has its own logging state; this replaces its root
    handlers with a single :class:`QueueHandler` so records travel back to
    the main process (consumed there by a :class:`QueueListener`).
    """
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(QueueHandler(log_queue))
    root.setLevel(logging.DEBUG)


@contextmanager
def worker_logging() -> Iterator[tuple[Any, Callable[[Any], None]]]:
    """Bridge worker-process logs into the main process's handlers.

    Usage::

        with worker_logging() as (log_queue, initializer):
            with ProcessPoolExecutor(
                initializer=initializer, initargs=(log_queue,)
            ) as ex:
                ...

    A :class:`QueueListener` over the current root handlers runs for the
    duration of the ``with`` block (``respect_handler_level=True``, so each
    handler's own level still filters). Yields ``(log_queue, initializer)``.
    """
    log_queue = Manager().Queue(-1)
    listener = QueueListener(
        log_queue, *logging.getLogger().handlers, respect_handler_level=True
    )
    listener.start()
    try:
        yield log_queue, _worker_log_init
    finally:
        listener.stop()
