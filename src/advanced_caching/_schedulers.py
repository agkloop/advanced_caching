"""Internal APScheduler singletons used by BGCache decorators.

These are internal to allow decorators.py to stay focused on caching semantics.
"""

from __future__ import annotations

import threading
import asyncio
from typing import ClassVar

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.asyncio import AsyncIOScheduler


class SharedScheduler:
    """Singleton `BackgroundScheduler` for sync BGCache jobs."""

    _scheduler: ClassVar[BackgroundScheduler | None] = None
    _lock: ClassVar[threading.RLock] = threading.RLock()
    _started: ClassVar[bool] = False

    @classmethod
    def get_scheduler(cls) -> BackgroundScheduler:
        with cls._lock:
            if cls._scheduler is None:
                cls._scheduler = BackgroundScheduler(daemon=True)
            assert cls._scheduler is not None
            return cls._scheduler

    @classmethod
    def start(cls) -> None:
        with cls._lock:
            if not cls._started:
                cls.get_scheduler().start()
                cls._started = True

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        with cls._lock:
            if cls._started and cls._scheduler is not None:
                cls._scheduler.shutdown(wait=wait)
                cls._started = False
                cls._scheduler = None


class SharedAsyncScheduler:
    """Singleton `AsyncIOScheduler` for AsyncBGCache jobs."""

    _scheduler: ClassVar[AsyncIOScheduler | None] = None
    _lock: ClassVar[threading.RLock] = threading.RLock()
    _started: ClassVar[bool] = False

    @classmethod
    def get_scheduler(cls) -> AsyncIOScheduler:
        with cls._lock:
            if cls._scheduler is None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    cls._scheduler = AsyncIOScheduler()
                else:
                    cls._scheduler = AsyncIOScheduler(event_loop=loop)
            assert cls._scheduler is not None
            return cls._scheduler

    @classmethod
    def ensure_started(cls) -> None:
        with cls._lock:
            if not cls._started:
                cls.get_scheduler().start()
                cls._started = True

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        with cls._lock:
            if cls._started and cls._scheduler is not None:
                try:
                    cls._scheduler.shutdown(wait=wait)
                except RuntimeError:
                    # Ignore shutdown if loop already closed (test teardown).
                    pass
                cls._started = False
                cls._scheduler = None
