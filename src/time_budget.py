"""Dynamic per-task time budget manager.

Total budget (default 4320 min) minus setup reserve (default 30 min) is split
across tasks.  Each task gets ``remaining_time / remaining_tasks`` seconds,
capped at a hard maximum.  Fast tasks bank time for harder ones.
"""

import logging
import time
import os

logger = logging.getLogger(__name__)


class TimeBudget:
    """Thread-safe (single-event-loop) time budget tracker."""

    def __init__(self) -> None:
        total_min = float(os.getenv("TIME_BUDGET_TOTAL_MIN", "4320"))
        reserve_min = float(os.getenv("TIME_BUDGET_RESERVE_MIN", "30"))
        self.total_tasks = int(os.getenv("TIME_BUDGET_TOTAL_TASKS", "2140"))
        self.cap_sec = float(os.getenv("TIME_BUDGET_CAP_SEC", "300"))  # 5 min hard cap

        self._budget_sec = (total_min - reserve_min) * 60
        self._remaining_sec = self._budget_sec
        self._remaining_tasks = self.total_tasks
        self._start_time = time.monotonic()
        self._task_start: float | None = None

        logger.info(
            f"TimeBudget: {self._budget_sec:.0f}s for {self.total_tasks} tasks, "
            f"cap={self.cap_sec:.0f}s"
        )

    @property
    def task_timeout(self) -> float:
        """Seconds allowed for the next task."""
        if self._remaining_tasks <= 0:
            return self.cap_sec
        dynamic = self._remaining_sec / self._remaining_tasks
        return min(max(dynamic, 10.0), self.cap_sec)  # floor 10s

    def start_task(self) -> float:
        """Mark task start. Returns the allowed seconds for this task."""
        self._task_start = time.monotonic()
        timeout = self.task_timeout
        logger.info(
            f"TimeBudget: task start — timeout={timeout:.1f}s, "
            f"remaining={self._remaining_sec:.0f}s, "
            f"tasks_left={self._remaining_tasks}"
        )
        return timeout

    def end_task(self) -> None:
        """Mark task end and update remaining budget."""
        if self._task_start is None:
            return
        elapsed = time.monotonic() - self._task_start
        self._remaining_sec = max(self._remaining_sec - elapsed, 0)
        self._remaining_tasks = max(self._remaining_tasks - 1, 0)
        self._task_start = None
        logger.info(
            f"TimeBudget: task done in {elapsed:.1f}s — "
            f"remaining={self._remaining_sec:.0f}s, "
            f"tasks_left={self._remaining_tasks}"
        )

    @property
    def exhausted(self) -> bool:
        return self._remaining_sec <= 0
