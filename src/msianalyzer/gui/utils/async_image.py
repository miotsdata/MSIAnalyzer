"""
async_image.py
The plumbing that lets a Python image provider render off the GUI thread
(ADR 50).

`QQuickAsyncImageProvider.requestImageResponse` only builds an
`ImageResponse` and queues a `_RenderJob` on the provider's pool, then
returns at once. The job renders (the provider's ordinary synchronous
`requestImage`), stores the result, and emits `finished`. Qt documents
that emitting from any thread is allowed.

Every caller passes a pool created with `new_render_pool()`, which has a
single thread. So renders run one at a time, in request order. That keeps
the guarantee the old `asynchronous: false` tiles relied on (no two
Python renders at once), without doing the work on the GUI thread.

A response Qt cancels, because the tile's source changed or the tile was
destroyed, skips its render. It still emits `finished`, as Qt requires,
so the engine can clean it up.
"""

from __future__ import annotations

import functools
import logging
import threading
from typing import Callable

from PySide6.QtCore import QRunnable, QThreadPool
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageResponse, QQuickTextureFactory

logger = logging.getLogger(__name__)


def new_render_pool() -> QThreadPool:
    pool = QThreadPool()
    pool.setMaxThreadCount(1)
    return pool


class ImageResponse(QQuickImageResponse):
    def __init__(self) -> None:
        super().__init__()
        self._image = QImage()
        self._cancelled = threading.Event()
        #: Set once `finished` has been emitted (lets tests wait without
        #: racing the signal).
        self.done = False

    def textureFactory(self) -> QQuickTextureFactory:
        return QQuickTextureFactory.textureFactoryForImage(self._image)

    def errorString(self) -> str:
        # A null image must reach QML as Image.Error, the same as a null
        # QImage from a synchronous provider. Without this, Qt accepts the
        # empty texture as a *Ready* 0x0 image, and a Ready 0x0 image in
        # ZoomableImage blocks all pointer input in its Flickable (a known
        # bug, already fixed once for the synchronous case).
        return "no image" if self._image.isNull() else ""

    def cancel(self) -> None:
        self._cancelled.set()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def complete(self, image: QImage) -> None:
        self._image = image
        self.done = True
        self.finished.emit()


# Every response still alive, by id. After `finished`, Qt still calls
# `textureFactory()` on the response and only then deletes it itself. If
# Python dropped its last reference earlier, the wrapper could delete the
# C++ object first (use-after-free; a test that did this segfaulted).
# Each response is kept until Qt's own `destroyed` fires.
_alive: dict[int, ImageResponse] = {}
_alive_lock = threading.Lock()


def _forget(key: int, *_args) -> None:
    with _alive_lock:
        _alive.pop(key, None)


class _RenderJob(QRunnable):
    def __init__(self, render: Callable[[], QImage], response: ImageResponse) -> None:
        super().__init__()
        self._render = render
        self._response = response
        self.setAutoDelete(True)

    def run(self) -> None:
        response = self._response
        image = QImage()
        try:
            if not response.cancelled:
                image = self._render()
        except Exception:
            logger.exception("async image render failed")
        finally:
            response.complete(image)


def start_render(pool: QThreadPool, render: Callable[[], QImage]) -> ImageResponse:
    """Queue `render` on `pool`, and return the response Qt waits on."""
    response = ImageResponse()
    key = id(response)
    with _alive_lock:
        _alive[key] = response
    response.destroyed.connect(functools.partial(_forget, key))
    pool.start(_RenderJob(render, response))
    return response
