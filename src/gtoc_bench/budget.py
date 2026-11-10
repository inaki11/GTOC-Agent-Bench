import threading
import time


class Budget:
    def __init__(self, wall_seconds, token_limit=None):
        self.wall_seconds = wall_seconds
        self.token_limit = token_limit
        self.started = None
        self.ended = None
        self.paused_at = None
        self.paused_seconds = 0.0
        self.tokens = 0
        self.unknown_requests = 0
        self.reason = None
        self.stopped = threading.Event()
        self.lock = threading.RLock()

    def start(self):
        with self.lock:
            self.started = time.monotonic()

    def stop(self, reason):
        with self.lock:
            if self.reason is None:
                self.reason = reason
                self.ended = time.monotonic()
                if self.paused_at is not None:
                    self.paused_seconds += self.ended - self.paused_at
                    self.paused_at = None
                self.stopped.set()

    def pause(self):
        with self.lock:
            if self.check() and self.paused_at is None:
                self.paused_at = time.monotonic()

    def resume(self):
        with self.lock:
            if self.paused_at is not None:
                self.paused_seconds += time.monotonic() - self.paused_at
                self.paused_at = None

    def timeline_elapsed(self):
        """Active event time, continuing through finalization as before."""
        with self.lock:
            if self.started is None:
                return 0.0
            now = time.monotonic()
            paused = self.paused_seconds + (
                now - self.paused_at if self.paused_at is not None else 0
            )
            return max(0.0, now - self.started - paused)

    def check(self):
        with self.lock:
            if self.started is not None and self.timeline_elapsed() >= self.wall_seconds:
                self.stop("time_limit")
            if self.token_limit is not None and self.tokens >= self.token_limit:
                self.stop("token_limit")
            return self.started is not None and not self.stopped.is_set()

    def add_usage(self, usage, *, provisional=False):
        with self.lock:
            self.tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            if usage.get("quality") != "reported" and not provisional:
                self.unknown_requests += 1
            self.check()

    def status(self):
        with self.lock:
            elapsed = (
                0.0 if self.started is None else (self.ended or time.monotonic()) - self.started
            )
            wall_elapsed = elapsed
            paused = self.paused_seconds + (
                time.monotonic() - self.paused_at if self.paused_at is not None else 0
            )
            elapsed = max(0.0, elapsed - paused)
            return {
                "elapsed_seconds": elapsed,
                "remaining_seconds": max(0, self.wall_seconds - elapsed),
                "wall_seconds": self.wall_seconds,
                "wall_elapsed_seconds": wall_elapsed,
                "paused_seconds": paused,
                "budget_paused": self.paused_at is not None,
                "token_limit": self.token_limit,
                "known_tokens": self.tokens,
                "unknown_usage_requests": self.unknown_requests,
                "remaining_tokens": None
                if self.token_limit is None
                else max(0, self.token_limit - self.tokens),
                "token_overshoot": 0
                if self.token_limit is None
                else max(0, self.tokens - self.token_limit),
                "stop_reason": self.reason,
            }
