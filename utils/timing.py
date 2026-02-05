import time
import logging
from contextvars import ContextVar
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

@dataclass
class LatencyTracker:
    """Tracks latency for a single request."""
    request_id: str
    start_time: float = field(default_factory=time.perf_counter)
    timings: dict[str, float] = field(default_factory=dict)

    def record(self, operation: str, duration_ms: float):
        """Record an operation's duration."""
        self.timings[operation] = duration_ms

    def get_summary(self) -> dict:
        """Get timing summary."""
        total_ms = (time.perf_counter() - self.start_time) * 1000
        operations_sum_ms = sum(self.timings.values())

        return {
            "request_id": self.request_id,
            "total_ms": round(total_ms, 2),
            "operations_sum_ms": round(operations_sum_ms, 2),  # Sum of all ops (may include parallel execution)
            "breakdown": {k: round(v, 2) for k, v in self.timings.items()}
        }

# Context variable for current request tracker
_current_tracker: ContextVar[LatencyTracker | None] = ContextVar('tracker', default=None)

def start_tracking(request_id: str) -> LatencyTracker:
    """Start tracking a request."""
    tracker = LatencyTracker(request_id=request_id)
    _current_tracker.set(tracker)
    return tracker

def end_tracking():
    """End tracking and log summary."""
    tracker = _current_tracker.get()
    if tracker:
        summary = tracker.get_summary()
        logger.info(f"[LATENCY] {summary}")
        _current_tracker.set(None)
        return summary

@asynccontextmanager
async def track(operation: str):
    """Track an operation's duration."""
    start = time.perf_counter()
    try:
        yield
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        tracker = _current_tracker.get()
        if tracker:
            tracker.record(operation, duration_ms)
