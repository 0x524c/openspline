"""Opt-in, dependency-free startup spans written as JSON lines to container stdout."""

import asyncio
import json
import os
import sys
import time

_PROCESS_STARTED = time.perf_counter()


class WarmupSpan:
    """Time host work and optionally drain CUDA work before completing a span.

    Only fixed diagnostic fields and exception types are emitted. Never pass
    tokens, URLs, portraits, audio, or exception messages to this logger.
    Disabled spans do not synchronize CUDA. A span can finish before its context
    exits, e.g. when a long-lived connection first becomes ready.
    """

    def __init__(
        self, stage, *, component="gpu", enabled=None, started=None,
        session_id=None, quality=None, gpu=None, worker_id=None, synchronize=None,
    ):
        self.enabled = (
            os.getenv("OPENSPLINE_WARMUP_TIMING") == "1" if enabled is None else enabled
        )
        self.stage = stage
        self.component = component
        self.started = time.perf_counter() if started is None else started
        self.synchronize = synchronize
        self.finished = False
        self.fields = {
            "session_id": session_id or os.getenv("OPENSPLINE_WARMUP_SESSION_ID"),
            "quality": quality or os.getenv("OPENSPLINE_WARMUP_QUALITY"),
            "gpu": gpu or os.getenv("OPENSPLINE_WARMUP_GPU"),
            "worker_id": worker_id,
            "rank": os.getenv("RANK"),
        }
        self._emit("started")

    def _emit(self, status, error=None):
        if not self.enabled:
            return
        now = time.perf_counter()
        record = {
            "event": "openspline.warmup",
            "component": self.component,
            "stage": self.stage,
            "status": status,
            "timestamp_ms": round(time.time() * 1000, 3),
            "pid": os.getpid(),
            "process_elapsed_ms": round((now - _PROCESS_STARTED) * 1000, 3),
            **{key: value for key, value in self.fields.items() if value is not None},
        }
        if status != "started":
            record["duration_ms"] = round((now - self.started) * 1000, 3)
        if error is not None:
            record["error_type"] = type(error).__name__
        try:
            sys.stdout.write(json.dumps(record, separators=(",", ":")) + "\n")
            sys.stdout.flush()
        except OSError:
            pass  # A closed log pipe must not prevent session cleanup.

    def finish(self, error=None):
        if self.finished:
            return
        self.finished = True
        if error is None and self.enabled and self.synchronize is not None:
            try:
                self.synchronize()
            except BaseException as exc:
                self._emit("error", exc)
                raise
        status = "ok" if error is None else (
            "cancelled" if isinstance(error, asyncio.CancelledError) else "error"
        )
        self._emit(status, error)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.finish(exc)
